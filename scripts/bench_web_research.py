"""Before/after timings of the web research's Phase 1 pull and of the grading source loads, on the
hermetic fakes with the providers' latencies scaled down (audit 2026-10-30, docs/audit/
2026-10-30-web-research-speed-and-coverage.md). No network, no database, no paid call.

    python scripts/bench_web_research.py pull old|new SCALE [caps_off] [nofollow]
    python scripts/bench_web_research.py sources old|new N BATCHES GET_MS

``old`` loads the code of ``--ref`` (default HEAD, ``git show``) for the modules the change
replaced; run it before committing the change, or give the commit before it as ``--ref``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
# the audit's latencies in seconds (docs/performance/2026-10-27-speed-audit.md; scrape: assumed)
LATENCY = {"search": 2.8, "read": 5.0, "scrape": 8.0, "feed": 3.0}


def load_old(ref: str, modules: dict[str, str]) -> None:
    """Replace the named modules by their text at ``ref`` (module name -> repository path)."""
    out = Path(tempfile.mkdtemp(prefix="bench-old-"))
    for name, path in modules.items():
        file = out / (name.replace(".", "_") + ".py")
        file.write_bytes(subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT))
        spec = importlib.util.spec_from_file_location(name, file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


async def pull(mode: str, scale: float, caps_off: bool, nofollow: bool, ref: str) -> None:
    """The 23 web rows pulled 8 at a time by ``PullAgent`` and the real ``ConnectorProvider``."""
    if caps_off:
        os.environ["RESEARCH_CAPS"] = "off"
    if mode == "old":
        import agents.production  # noqa: F401
        import agents.pull  # noqa: F401

        load_old(
            ref,
            {
                "agents.production.provider": "src/agents/production/provider.py",
                "agents.pull.agent": "src/agents/pull/agent.py",
            },
        )
    import agents.production.provider as pm
    from account_intelligence.connectors.base import ConnectorResult, Operation
    from account_intelligence.connectors.models import ScrapedPageRecord
    from agents.config import load_skill_config
    from agents.contracts import BudgetMeter, load_registry
    from agents.pull.agent import PullAgent
    from crosscut.fanout import fan_out
    from tests.agents.fakes import FakeClock, FakePayloads
    from tests.agents.test_production_adapters import ctx, page, prov

    calls: Counter[str] = Counter()
    flying: Counter[str] = Counter()
    peak: Counter[str] = Counter()
    slots = {"p_search": asyncio.Semaphore(8), "p_scrape": asyncio.Semaphore(4)}

    async def timed(kind: str, provider: str) -> None:
        async with slots.get(provider) or asyncio.Lock():
            flying[provider] += 1
            peak[provider] = max(peak[provider], flying[provider])
            await asyncio.sleep(LATENCY[kind] * scale)
            flying[provider] -= 1

    async def fake(route, args, cc, rt, *, operation=None):  # type: ignore[no-untyped-def]
        calls[route] += 1
        if route == "open_web_search":
            await timed("search", "p_search")
            tag = abs(hash(args.queries[0])) % 10**6
            own = bool(args.include_domains)
            records = tuple(
                page(f"https://acme.example/{tag}/p{i}") for i in range(6 if own else 3)
            )
            if not own:
                records += tuple(page(f"https://host{i}.example/{tag}") for i in range(7))
            return ConnectorResult(status="ok", records=records, call_refs=(f"s{calls[route]}",))
        if route == "job_boards_and_pages":
            await timed("scrape", "p_scrape")
            links = "\n".join(
                f"[R](/news/2026/05/r-{abs(hash(args.url)) % 1000}-{i})" for i in range(12)
            )
            record = ScrapedPageRecord(
                kind="scraped_page",
                provenance=prov(args.url, Operation.SCRAPE_JOB_PAGE),
                lineage_hint=None,
                requested_url=args.url,
                final_url=args.url,
                target_status=200,
                markdown_text="Acme " + links,
                title_text="t",
                publish_date=None,
            )
            return ConnectorResult(status="ok", records=(record,), call_refs=(f"c{calls[route]}",))
        if route == "read_known_page":
            await timed("read", "p_search")
            return ConnectorResult(
                status="ok", records=(page(args.url),), call_refs=(f"r{calls[route]}",)
            )
        await timed("feed", "p_signals")
        return ConnectorResult(
            status="ok", records=(page("https://acme.example/news/1"),), call_refs=("f1",)
        )

    pm.call_need = fake
    cfg = load_skill_config()
    if nofollow:
        from dataclasses import replace

        cfg = replace(
            cfg, plan={k: replace(v, follow=None, also_on=None) for k, v in cfg.plan.items()}
        )

    class Runtime:
        """A stand-in runtime: the fake ``call_need`` never touches it."""

    provider = pm.ConnectorProvider(Runtime(), uuid4(), skill=cfg)  # type: ignore[arg-type]
    context = ctx()
    agent = PullAgent(load_registry()["pull"], cfg, provider, FakePayloads(), FakeClock(), None)
    meter = BudgetMeter(load_registry()["pull"], started_at=FakeClock().now())
    todo = [
        k
        for k, r in cfg.plan.items()
        if not r.people and not r.internal_history and r.pipeline is None
    ]
    durations: dict[str, float] = {}

    async def one(key: str):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        try:
            return await agent.pull_row(
                context, cfg.plan[key], meter, consent_problem=None, internal_history=False
            )
        finally:
            durations[key] = (time.monotonic() - started) / scale

    started = time.monotonic()
    rows = await fan_out(todo, one, stop=lambda: False)
    wall = time.monotonic() - started
    slow = sorted(durations.items(), key=lambda kv: -kv[1])[:4]
    print("slowest rows (s, real-time equivalent):", [(k, round(v)) for k, v in slow])
    print(
        f"{mode} scale={scale} caps_off={caps_off} nofollow={nofollow} rows={len(todo)} "
        f"stored={sum(r.status == 'stored' for r in rows)} "
        f"items={sum(len(r.payload_refs) for r in rows)} "
        f"calls={dict(calls)} total={sum(calls.values())} peak={dict(peak)} "
        f"wall={wall:.2f}s => real-time equivalent {wall / scale:.0f}s"
    )


async def sources(mode: str, n: int, batches: int, get_ms: float, ref: str) -> None:
    """``LedgerAdapter.sources`` over a fake ledger of ``n`` payloads, loaded ``batches`` times."""
    import account_intelligence.grading.adapters as adapters
    from account_intelligence.connectors.base import Operation, ProviderCode
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.grading.ports import RunInfo

    module = adapters
    if mode == "old":
        load_old(ref, {"bench_old_adapters": "src/account_intelligence/grading/adapters.py"})
        module = sys.modules["bench_old_adapters"]
    gets = [0]

    class Store:
        async def get(self, key: str) -> bytes:
            gets[0] += 1
            await asyncio.sleep(get_ms / 1000)
            return b"x" * 2000

    class Connector:
        def parse(self, op, args, raw, base, cursor):  # type: ignore[no-untyped-def]
            start = time.perf_counter()
            while time.perf_counter() - start < 0.002:  # 2 ms of parse work per payload
                pass
            source = types.SimpleNamespace(source_url="https://a.example/p")
            return types.SimpleNamespace(
                records=[types.SimpleNamespace(provenance=source, lineage_hint=None)]
            )

    module.ARGS_FOR = {
        Operation.WEB_SEARCH: types.SimpleNamespace(model_validate_json=lambda s: None)
    }
    module.record_text = lambda r: "text"
    module.record_published = lambda r: None
    rows = [
        types.SimpleNamespace(
            args_hash=f"h{i:05d}",
            provider_code="p_search",
            operation="web_search",
            response={"args": {}, "retrieved_at": datetime.now(UTC).isoformat()},
            response_ref=f"ref/{i}",
            created_at=None,
        )
        for i in range(n)
    ]

    class Adapter(module.LedgerAdapter):
        async def _rows(self, run):  # type: ignore[no-untyped-def]
            return rows

    adapter = Adapter(None, Store(), {ProviderCode.P_SEARCH: Connector()}, load_grading_config())
    info = RunInfo(uuid4(), uuid4(), uuid4(), datetime.now(UTC), "a.example", "A")
    started = time.monotonic()
    found: list = []
    for _ in range(batches):
        found = list(await adapter.sources(info))
    print(
        f"{mode}: N={n} batches={batches} get={get_ms}ms parse=2ms -> gets={gets[0]} "
        f"sources={len(found)} wall={time.monotonic() - started:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=("pull", "sources"))
    parser.add_argument("mode", choices=("old", "new"))
    parser.add_argument("numbers", nargs="*")
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    if args.what == "pull":
        scale = float(args.numbers[0])
        flags = set(args.numbers[1:])
        asyncio.run(pull(args.mode, scale, "caps_off" in flags, "nofollow" in flags, args.ref))
    else:
        n, batches, get_ms = int(args.numbers[0]), int(args.numbers[1]), float(args.numbers[2])
        asyncio.run(sources(args.mode, n, batches, get_ms, args.ref))


if __name__ == "__main__":
    main()
