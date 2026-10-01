#!/usr/bin/env python3
"""Spike 3 (T12): measure prompt-cache read ratio over 10 identical-prompt calls.

Pass criterion: cache-read ratio >= 0.70 from the second call onward, and no
invalidation across slices of >= 6 minutes (when run with --gap).

Providers:
  dry_run (default) : simulates a cache with deterministic miss-then-hit behaviour;
                      no network, used by CI.
  live              : calls a real chat-completions API using SPIKE3_API_KEY and
                      SPIKE3_BASE_URL (OpenAI-compatible). Token usage with
                      cached_tokens is read from the response.

Usage:
    python spikes/spike3_cache/run.py --provider dry_run --calls 10
    python spikes/spike3_cache/run.py --provider live --model <id>
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

PASS_RATIO = 0.70
CALLS_DEFAULT = 10

# Stable prefix per the prompt-caching playbook (Spec 10.2):
# tools first (sorted), then system/SOP, then reference text; tenant data comes last.
TOOLS = sorted(
    [
        {"name": "resolve_domain", "desc": "Resolve a company name to a canonical domain."},
        {"name": "fetch_news", "desc": "Fetch recent news for a domain."},
        {"name": "fetch_jobs", "desc": "Fetch job postings for a domain."},
        {"name": "fetch_people", "desc": "Fetch leadership signals for a domain."},
    ],
    key=lambda t: t["name"],
)
SYSTEM_SOP = "You are the ABM research planner. Follow the standing operating procedure v1." * 6
REFERENCE = "Coverage rows reference: " + ", ".join(f"row-{i}" for i in range(1, 29))


@dataclass
class CallRecord:
    """Token accounting for one model call."""

    index: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    latency_ms: int

    @property
    def cache_read_ratio(self) -> float:
        if self.input_tokens == 0:
            return 0.0
        return self.cached_tokens / self.input_tokens


@dataclass
class Measurement:
    """Full spike result."""

    provider: str
    model: str
    records: list[CallRecord] = field(default_factory=list)

    def ratios_from_second_call(self) -> list[float]:
        return [r.cache_read_ratio for r in self.records[1:]] or [0.0]

    def min_ratio_from_second_call(self) -> float:
        return min(self.ratios_from_second_call())

    def mean_ratio_from_second_call(self) -> float:
        return statistics.mean(self.ratios_from_second_call())

    def passes(self) -> bool:
        return bool(self.records) and self.min_ratio_from_second_call() >= PASS_RATIO


class Provider(Protocol):
    """A cache-aware model provider."""

    async def call(self, index: int, prompt_tail: str) -> CallRecord:
        """Issue one call and return its token accounting."""
        ...


class DryRunProvider:
    """Simulates provider caching: first call misses, later calls hit the stable prefix.

    Simulates silent invalidation with probability 0 so the dry run demonstrates the
    passing configuration; use --invalidate to demonstrate the failing configuration.
    """

    def __init__(self, prefix_tokens: int = 4200, invalidate: bool = False) -> None:
        self.prefix_tokens = prefix_tokens
        self.invalidate = invalidate
        self._cache_alive = False

    async def call(self, index: int, prompt_tail: str) -> CallRecord:
        tail_tokens = max(1, len(prompt_tail) // 4)
        if index > 0 and self._cache_alive:
            cached = self.prefix_tokens
        else:
            cached = 0
            self._cache_alive = True
        if self.invalidate and index in (4, 7):
            cached = 0  # timestamp-like change in the prefix invalidates the cache
        start = time.monotonic()
        await _tiny_sleep()
        return CallRecord(
            index=index,
            input_tokens=self.prefix_tokens + tail_tokens,
            cached_tokens=cached,
            output_tokens=64,
            latency_ms=int((time.monotonic() - start) * 1000) + 3,
        )


class LiveProvider:
    """OpenAI-compatible chat completions with cached token reporting."""

    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _payload(self, tail: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{json.dumps(TOOLS)}\n{SYSTEM_SOP}\n{REFERENCE}"},
                {"role": "user", "content": tail},
            ],
            "max_tokens": 64,
        }

    async def call(self, index: int, prompt_tail: str) -> CallRecord:
        import urllib.error
        import urllib.request

        body = json.dumps(self._payload(prompt_tail)).encode()
        request = urllib.request.Request(  # noqa: S310
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        usage = data.get("usage", {})
        return CallRecord(
            index=index,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )


async def _tiny_sleep() -> None:
    import asyncio

    await asyncio.sleep(0.001)


def build_provider(name: str, model: str, invalidate: bool) -> Provider:
    """Construct the provider by name (dry_run needs no network or key)."""
    if name == "dry_run":
        return DryRunProvider(invalidate=invalidate)
    if name == "live":
        api_key = os.environ.get("SPIKE3_API_KEY", "")
        base = os.environ.get("SPIKE3_BASE_URL", "https://api.openai.com/v1")
        if not api_key:
            msg = "SPIKE3_API_KEY is required for --provider live (set it in .env)"
            raise SystemExit(msg)
        return LiveProvider(model or "gpt-4.1-mini", base, api_key)
    msg = f"unknown provider {name!r} (use dry_run|live)"
    raise SystemExit(msg)


def run_gap_check(provider: Provider, gap_seconds: float) -> bool:
    """Pause longer than the default cache lifetime between two calls; cache must survive
    when a long-TTL tier is configured (measured live; dry run asserts the invariant)."""
    import asyncio

    async def one() -> float:
        await provider.call(90, "tail for gap check a")
        time.sleep(gap_seconds)
        after = await provider.call(91, "tail for gap check b")
        return after.cache_read_ratio

    return asyncio.run(one()) >= PASS_RATIO


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="dry_run", choices=["dry_run", "live"])
    parser.add_argument("--model", default="")
    parser.add_argument("--calls", type=int, default=CALLS_DEFAULT)
    parser.add_argument("--invalidate", action="store_true", help="simulate silent invalidation")
    parser.add_argument("--gap", type=float, default=0.0, help="seconds to pause mid-run")
    parser.add_argument("--json-out", default="")
    options = parser.parse_args()

    provider = build_provider(options.provider, options.model, options.invalidate)
    records: list[CallRecord] = []
    import asyncio

    async def drive() -> list[CallRecord]:
        out: list[CallRecord] = []
        for i in range(options.calls):
            rec = await provider.call(i, f"run-specific tail #{i}")
            out.append(rec)
            if options.gap and i == options.calls // 2:
                time.sleep(options.gap)
        return out

    records = asyncio.run(drive())
    measurement = Measurement(
        provider=options.provider, model=options.model or "sim", records=records
    )

    print(f"provider={options.provider} model={measurement.model} calls={len(records)}")
    for rec in records:
        print(
            f"  call {rec.index}: input={rec.input_tokens} cached={rec.cached_tokens}"
            f" ratio={rec.cache_read_ratio:.2f} latency={rec.latency_ms}ms"
        )
    mean_ratio = measurement.mean_ratio_from_second_call()
    min_ratio = measurement.min_ratio_from_second_call()
    print(f"mean ratio (call 2+) = {mean_ratio:.2f}; min = {min_ratio:.2f}; pass >= {PASS_RATIO}")
    passed = measurement.passes()
    if options.gap:
        gap_ok = run_gap_check(provider, options.gap)
        print(f"gap check ({options.gap}s pause): {'PASS' if gap_ok else 'FAIL'}")
        passed = passed and gap_ok
    print("SPIKE3 RESULT:", "PASS" if passed else "FAIL")
    if options.json_out:
        payload = {
            "provider": options.provider,
            "model": measurement.model,
            "records": [rec.__dict__ for rec in records],
            "mean_ratio_from_second_call": round(mean_ratio, 4),
            "min_ratio_from_second_call": round(min_ratio, 4),
            "pass": passed,
        }
        with open(options.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
