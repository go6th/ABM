"""Prompt-cache warm gate: one call warms a shared prefix before its siblings read it.

A cache entry becomes readable only once the request that writes it has begun its response, so N
calls sent together with the same cached prefix each pay to WRITE it and none reads it. Research
runs do exactly that: an agent fans out over sources, and every research task kind goes through
its own one-request Message Batch, all submitted at once; each wrote the same skill-and-schema
system block at the one-hour price (2x input) and almost none read it.

``WarmGate`` coordinates calls per prefix within one process (``prefix_key``: model, effort,
output format, every block up to the last breakpoint):

* the prefix is warm (a call on it read or wrote the cache within its lifetime): go at once;
* a leader is warming it: wait until the leader's response has started (bounded by
  ``max_wait_s``), then go and read it;
* otherwise this call leads. A call bound for a batch first waits ``coalesce_s`` for siblings:
  alone it releases the gate and is batched as before; with siblings waiting it is sent as one
  standard streamed call (the batch discount is given up on that call only) and releases them at
  its first streamed event, so their batches read a one-hour entry it wrote.

A prefix shorter than the model's cacheable minimum is never cached by the API (no error, no
write); such calls are not gated. Token counts are estimated at four characters a token, which
undercounts JSON and so errs toward not gating.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# a model whose ``min_cache_tokens`` models.yaml does not give is held to the strictest minimum
# the API documents (prompt caching docs, read 2026-10-21)
DEFAULT_MIN_CACHE_TOKENS = 4096
CHARS_PER_TOKEN = 4
TTL_SECONDS = {"5m": 300.0, "1h": 3600.0}


@lru_cache(maxsize=1)
def _minimums() -> dict[str, int]:
    """``min_cache_tokens`` of every model in ``config/models.yaml`` that gives one."""
    from account_intelligence.config import CONFIG_DIR

    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    return {
        str(model): int(spec["min_cache_tokens"])
        for model, spec in (data.get("models") or {}).items()
        if isinstance(spec, Mapping) and spec.get("min_cache_tokens") is not None
    }


def min_cache_tokens(model: str) -> int:
    """The smallest prefix the API caches for ``model`` (models.yaml ``min_cache_tokens``)."""
    return _minimums().get(model, DEFAULT_MIN_CACHE_TOKENS)


@dataclass(frozen=True)
class Prefix:
    """The cached prefix of one request: its identity, estimated size and lifetime."""

    key: str
    tokens: int
    ttl: str


def prefix_key(params: Mapping[str, Any]) -> Prefix | None:
    """The cached prefix of a ``messages.create`` request, or None when it has no breakpoint or
    the prefix is too short for the model to cache. Covers everything rendered ahead of the last
    ``cache_control`` block (tools, system, then message content) plus the settings rendered into
    the prompt (model, effort and output format)."""
    blocks: list[Any] = []
    ttl = ""
    found = False
    for tool in params.get("tools") or ():
        blocks.append(tool)
    system = params.get("system")
    sequence: list[Any] = list(system) if isinstance(system, list) else [system or ""]
    for message in params.get("messages") or ():
        content = message.get("content")
        sequence.extend(content if isinstance(content, list) else [content])
    upto = -1
    for index, block in enumerate(sequence):
        if isinstance(block, Mapping) and block.get("cache_control"):
            upto = index
            ttl = str(block["cache_control"].get("ttl", "5m"))
            found = True
    if not found:
        return None
    blocks.extend(sequence[: upto + 1])
    body = json.dumps(
        [params.get("model"), params.get("output_config"), blocks],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    tokens = sum(_chars(b) for b in blocks) // CHARS_PER_TOKEN
    if tokens < min_cache_tokens(str(params.get("model") or "")):
        return None
    return Prefix(hashlib.sha256(body.encode()).hexdigest(), tokens, ttl or "5m")


def _chars(block: Any) -> int:
    if isinstance(block, str):
        return len(block)
    if isinstance(block, Mapping) and isinstance(block.get("text"), str):
        return len(block["text"])
    return len(json.dumps(block, default=str))


@dataclass
class _State:
    warm_until: float = 0.0
    leader: asyncio.Future[None] | None = None
    waiters: int = 0


@dataclass
class Lead:
    """This call warms the prefix: ``standard`` says to send it as one standard streamed call
    (siblings are waiting); ``release`` lets them go and must be called exactly once."""

    gate: WarmGate
    key: str
    standard: bool
    future: asyncio.Future[None] = field(repr=False)

    def release(self) -> None:
        """Let the waiting siblings go (idempotent)."""
        if not self.future.done():
            self.future.set_result(None)


class WarmGate:
    """Per-process coordination of calls that share a cached prefix (see the module doc)."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        coalesce_s: float = 0.25,
        max_wait_s: float = 120.0,
        margin_s: float = 30.0,
        max_keys: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.coalesce_s, self.max_wait_s, self.margin_s = coalesce_s, max_wait_s, margin_s
        self.max_keys, self.clock = max_keys, clock
        self._states: OrderedDict[str, _State] = OrderedDict()

    def _state(self, key: str) -> _State:
        state = self._states.get(key)
        if state is None:
            state = self._states[key] = _State()
            if len(self._states) > self.max_keys:  # one key added, so one evicted at most
                self._states.popitem(last=False)
        self._states.move_to_end(key)
        return state

    async def enter(self, prefix: Prefix, *, batched: bool) -> tuple[str, Lead | None]:
        """("warm" | "follow" | "lead", the lead when this call leads)."""
        state = self._state(prefix.key)
        if state.warm_until > self.clock():
            return "warm", None
        leader = state.leader
        loop = asyncio.get_running_loop()
        if leader is not None and not leader.done() and leader.get_loop() is loop:
            state.waiters += 1
            try:
                await asyncio.wait_for(asyncio.shield(leader), timeout=self.max_wait_s)
            except TimeoutError:
                pass
            finally:
                state.waiters -= 1
            return "follow", None
        future: asyncio.Future[None] = loop.create_future()
        state.leader, state.waiters = future, 0
        lead = Lead(self, prefix.key, standard=True, future=future)
        if batched:
            # a lone batched call keeps its discount; siblings arriving in this window wait
            await asyncio.sleep(self.coalesce_s)
            if state.waiters == 0:
                lead.release()
                return "lead", Lead(self, prefix.key, standard=False, future=future)
        return "lead", lead

    def warmed(self, prefix: Prefix, read: int, written: int, started: float) -> None:
        """Record that the prefix is in the cache (a call on it read or wrote it at ``started``);
        later calls go without waiting until shortly before the entry would expire. A read
        refreshes the entry's lifetime."""
        if read <= 0 and written <= 0:
            return
        until = started + TTL_SECONDS.get(prefix.ttl, 300.0) - self.margin_s
        state = self._state(prefix.key)
        state.warm_until = max(state.warm_until, until)


class ReadRatio:
    """The cache-read share of input tokens per tier over a rolling window (one hour by
    default: the ``cache_ratio_low`` alert's window): reads / (reads + writes + uncached)."""

    def __init__(
        self, window_s: float = 3600.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.window_s, self.clock = window_s, clock
        self._calls: dict[str, deque[tuple[float, int, int, int]]] = {}

    def add(self, tier: str, read: int, written: int, uncached: int) -> float | None:
        """Count one call; the tier's share over the window, None while it has no input."""
        now = self.clock()
        calls = self._calls.setdefault(tier, deque())
        calls.append((now, read, written, uncached))
        # LOOP(cache ratio window): cap=the calls of one window; exit=oldest call inside it
        while calls and calls[0][0] < now - self.window_s:
            calls.popleft()
        total = sum(r + w + u for _, r, w, u in calls)
        return sum(r for _, r, _, _ in calls) / total if total else None


RATIO = ReadRatio()


def gate_from_config(path: Path | None = None) -> WarmGate:
    """The gate as ``prompt_cache_gate`` of ``config/models.yaml`` sets it (absent: on, with
    the defaults); a bad value is refused at start-up, not at the first call."""
    from account_intelligence.config import CONFIG_DIR

    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    raw = data.get("prompt_cache_gate") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("models.yaml prompt_cache_gate must be a mapping")
    unknown = set(raw) - {"enabled", "coalesce_seconds", "max_wait_seconds"}
    if unknown:
        raise ValueError(f"models.yaml prompt_cache_gate has unknown keys {sorted(unknown)}")
    coalesce = float(raw.get("coalesce_seconds", 0.25))
    max_wait = float(raw.get("max_wait_seconds", 120.0))
    if not 0 <= coalesce <= 5 or not 0 < max_wait <= 600:
        raise ValueError("models.yaml prompt_cache_gate: coalesce 0-5 s, max wait 0-600 s")
    return WarmGate(
        enabled=bool(raw.get("enabled", True)), coalesce_s=coalesce, max_wait_s=max_wait
    )


GATE = gate_from_config()


class SystemPrompt(str):
    """A system prompt in cacheable parts, most widely shared first. As a string it is the parts
    joined by blank lines (what a provider without cache blocks reads, and what reply keys hash);
    the Anthropic port sends each part as its own system block with a cache breakpoint, so calls
    that share only the leading parts (other task kinds over the same session document and
    skill) still read those from the cache."""

    parts: tuple[str, ...]

    def __new__(cls, parts: tuple[str, ...] | list[str]) -> SystemPrompt:
        kept = tuple(p for p in parts if p)
        prompt = super().__new__(cls, "\n\n".join(kept))
        prompt.parts = kept
        return prompt

    def extended(self, text: str) -> SystemPrompt:
        """The prompt with ``text`` appended to its last (least shared) part."""
        if not self.parts:
            return SystemPrompt((text,))
        return SystemPrompt((*self.parts[:-1], f"{self.parts[-1]}\n\n{text}"))


MAX_BREAKPOINTS = 4


def system_blocks(system: str, cache: Mapping[str, Any], reserved: int = 0) -> list[dict[str, Any]]:
    """The system prompt as cached text blocks: one per ``SystemPrompt`` part (leading parts
    merged when the parts outnumber the breakpoints left after ``reserved``), else one block."""
    parts = list(getattr(system, "parts", None) or (str(system),))
    room = max(1, MAX_BREAKPOINTS - reserved)
    if len(parts) > room:
        parts = ["\n\n".join(parts[: len(parts) - room + 1]), *parts[len(parts) - room + 1 :]]
    return [{"type": "text", "text": part, "cache_control": dict(cache)} for part in parts]
