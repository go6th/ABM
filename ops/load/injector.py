"""Load profile (HD2, D-80): a synthetic injector against stub providers. REPORT ONLY: no pass or
fail target is asserted except the sourced alert-freshness SLO, which is reported against.

Profile (stated in the report): ``tenants`` synthetic tenants, ``accounts`` each, one probe per
account per virtual week for ``weeks`` weeks with ``concurrency`` probes at a time (the WorldScript
fake world gives every account a posting and a news story), then ``reads`` API reads of the alert
inbox and the signal pages by signed-in members through the real application (in process, httpx
ASGI transport: no network, so latencies exclude TLS and load balancers).

k6 is not installed in this environment; ``ops/load/k6_profile.js`` holds the same profile for a
staging run ([VERIFY] k6 options).

Usage: ``TEST_DB=... python -m ops.load.injector --out docs/phase4/load_report.md``
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from datetime import timedelta
from typing import Any


def pct(xs: list[float], p: float) -> float:
    """The p-th percentile (nearest rank)."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(p / 100 * len(s))) - 1))]


async def run(
    tenants: int, accounts: int, weeks: int, concurrency: int, reads: int
) -> dict[str, Any]:
    """The profile; the measured numbers."""
    import yaml

    import tests.conftest  # noqa: F401  (the scratch test database settings, as the suite uses)
    from account_intelligence.config import CONFIG_DIR
    from account_intelligence.db.session import tenant_txn
    from alerts import freshness, outbox
    from refresh import escalate, probe
    from signals.registry import KINDS
    from tests.fakes.grader import FakeGrader
    from tests.fakes.phase3 import FakeClock
    from tests.fakes.world import EPOCH, FakeMonitor, Happening, WorldScript
    from tests.phase3.conftest import Browser, add_member, add_oidc_connection, make_app, uid
    from tests.phase4.conftest import tenant_with_account

    cfg = yaml.safe_load((CONFIG_DIR / "refresh.yaml").read_text())
    rules = escalate.Rules(
        cfg["escalation"]["delta_score_threshold"],
        cfg["escalation"]["max_brief_age_days"],
        cfg["escalation"]["min_interval_days"],
        cfg["escalation"]["max_per_period"],
    )
    clock = FakeClock()
    world = WorldScript()
    import psycopg

    from tests.conftest import ADMIN_DSN

    targets = []
    for ti in range(tenants):
        t, first = tenant_with_account(f"load-{ti}", f"acct-{ti}-0.example")
        for ai in range(accounts):  # LOOP(load accounts): cap=accounts; exit=end
            dom = f"acct-{ti}-{ai}.example"
            if ai == 0:
                a = first
            else:
                with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
                    a = c.execute(
                        "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
                        (t, dom),
                    ).fetchone()[0]
            world.happenings += [
                Happening(
                    dom,
                    "build_role_postings",
                    f"p-{ti}-{ai}",
                    "build_role_posting",
                    "X",
                    "Platform owner",
                    1,
                    None,
                    ("https://j.example",),
                    "t1",
                ),
                Happening(
                    dom,
                    "news_events",
                    f"n-{ti}-{ai}",
                    "expansion",
                    "X",
                    "Plant",
                    2,
                    None,
                    ("https://n1.example", "https://n2.example"),
                    "t2",
                ),
            ]
            targets.append((t, a, dom))
    probe_lat: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(t: Any, a: Any, dom: str) -> None:
        deps = probe.Deps(
            {k: FakeMonitor(k, world, clock) for k in KINDS},
            FakeGrader(),
            ["load-user"],
            "https://app.test",
            "standard",
            1,
            cfg["delta_weights"],
            rules,
            2,
            0.8,
            0.5,
            7,
        )
        async with sem:
            t0 = time.perf_counter()
            await probe.run_probe(
                tenant_txn,
                t,
                account_id=a,
                domain=dom,
                kinds=KINDS,
                brief_built_at=EPOCH,
                priority_a=False,
                account_score=1,
                slot_id=None,
                deps=deps,
                now=clock.current,
            )
            probe_lat.append(time.perf_counter() - t0)

    t_all = time.perf_counter()
    for wk in range(weeks):  # LOOP(load weeks): cap=weeks; exit=end
        clock.current = EPOCH + timedelta(weeks=wk)
        await asyncio.gather(*(one(*x) for x in targets))
        for t in sorted({x[0] for x in targets}, key=str):  # deliver as the relay job would
            async with tenant_txn(t) as s:
                await outbox.deliver_in_app(s, now=clock.current + timedelta(minutes=5))
    probe_wall = time.perf_counter() - t_all
    plane = make_app()
    browser = Browser(plane)
    t0_tenant = targets[0][0]
    slug = f"load-{uid()}"
    add_oidc_connection(t0_tenant, plane, slug)
    add_member(t0_tenant, f"{slug}:reader", "viewer")
    await browser.oidc_sign_in(slug, "reader", extra={"groups": ["readers"]})
    read_lat: list[float] = []
    errors = 0
    rsem = asyncio.Semaphore(concurrency)

    async def read(i: int) -> None:
        nonlocal errors
        async with rsem:
            t0 = time.perf_counter()
            r = await browser.client.get("/v1/signals" if i % 2 else "/v1/alerts")
            read_lat.append(time.perf_counter() - t0)
            errors += r.status_code >= 400

    await asyncio.gather(*(read(i) for i in range(reads)))
    await browser.close()
    async with tenant_txn(t0_tenant) as s:
        fr = freshness.compute(await outbox.freshness_rows(s), clock.current + timedelta(days=1))
    return {
        "profile": {
            "tenants": tenants,
            "accounts_per_tenant": accounts,
            "weeks": weeks,
            "concurrency": concurrency,
            "api_reads": reads,
        },
        "probes": len(probe_lat),
        "probe_wall_s": round(probe_wall, 2),
        "probe_ms": {p: round(pct(probe_lat, p) * 1000, 1) for p in (50, 95, 99)},
        "probe_mean_ms": round(statistics.mean(probe_lat) * 1000, 1) if probe_lat else None,
        "read_ms": {p: round(pct(read_lat, p) * 1000, 1) for p in (50, 95, 99)},
        "read_errors": errors,
        "freshness_sli_tenant0": {
            "good": fr.good,
            "judged": fr.judged,
            "pending": fr.pending,
            "slo": 0.95,
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--tenants", type=int, default=5)
    ap.add_argument("--accounts", type=int, default=10)
    ap.add_argument("--weeks", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--reads", type=int, default=400)
    ap.add_argument("--out", default="docs/phase4/load_report.md")
    a = ap.parse_args(argv)
    res = asyncio.run(run(a.tenants, a.accounts, a.weeks, a.concurrency, a.reads))
    lines = [
        "# Load profile (report only)",
        "",
        "Synthetic injector against stub providers and a local PostgreSQL 16 container "
        "(laptop). In-process HTTP (no network, no TLS). No target is asserted; the sourced "
        "alert-freshness SLO (95% within 24 h) is reported against. k6 is not installed "
        "here; `ops/load/k6_profile.js` is the same profile for staging.",
        "",
        "```json",
        __import__("json").dumps(res, indent=2),
        "```",
        "",
    ]
    with open(a.out, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
