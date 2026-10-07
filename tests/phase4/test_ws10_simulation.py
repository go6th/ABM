"""WS10 months-long simulation (harness item 1, RF1 to RF8, SM, DS, AL, CG). SYNTHETIC tenants,
the WorldScript fake world, a virtual clock. Three tenants, 26 virtual weeks. The expected probes,
escalations, removals, blocked rows and alerts are written by hand below (the oracle); the run
must match exactly, and must give the same canonical result under shuffled interleavings.
"""

from __future__ import annotations

import os
import random
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from alerts import subscriptions
from alerts.rules import load as load_rules
from refresh import escalate, planner, policy, probe, records, schedule, slots
from signals.registry import KINDS
from tests.fakes.grader import FakeGrader
from tests.fakes.phase3 import FakeClock
from tests.fakes.world import EPOCH, FakeMonitor, Happening, WorldScript
from tests.phase4.conftest import db, tenant_with_account

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
WEEKS = 26
CFG = yaml.safe_load((CONFIG_DIR / "refresh.yaml").read_text())
RULES = escalate.Rules(
    **{
        k: v
        for k, v in {
            "threshold": CFG["escalation"]["delta_score_threshold"],
            "max_brief_age_days": CFG["escalation"]["max_brief_age_days"],
            "min_interval_days": CFG["escalation"]["min_interval_days"],
            "max_per_period": CFG["escalation"]["max_per_period"],
        }.items()
    }
)


def world(suffix: str) -> WorldScript:
    a, b = f"alpha-{suffix}.example", f"beta-{suffix}.example"
    return WorldScript(
        happenings=[
            # A: a build role posting reported by two outlets, gone after week 9
            Happening(
                a,
                "build_role_postings",
                "post-1",
                "build_role_posting",
                "Alpha",
                "Salesforce platform owner",
                2,
                10,
                ("https://jobs-1.example", "https://jobs-2.example"),
                "t1",
            ),
            # A: a leadership change from one low-tier source (never immediate)
            Happening(
                a,
                "leadership_changes",
                "lead-1",
                "leadership_change",
                "Alpha",
                "New CIO named",
                5,
                None,
                ("https://blog.example",),
                "t3",
            ),
            # A: tier-one news from three outlets (one event, one alert)
            Happening(
                a,
                "news_events",
                "news-1",
                "expansion",
                "Alpha",
                "Alpha opens a plant",
                8,
                None,
                ("https://n1.example", "https://n2.example", "https://n3.example"),
                "t1",
            ),
            # A: a keyword match (a Lead, digest only)
            Happening(
                a,
                "build_role_postings",
                "kw-1",
                "keyword_match",
                "Alpha",
                "Mentions platform admin",
                14,
                None,
                ("https://board.example",),
                "t2",
            ),
            # A: an unverifiable candidate (dropped with a reason)
            Happening(a, "news_events", "rumour-1", "unverifiable", "Alpha", "Rumour", 16, 17),
            # B: a probable posting from week 3, forever
            Happening(
                b,
                "build_role_postings",
                "post-b",
                "build_role_posting",
                "Beta",
                "SAP basis lead",
                3,
                None,
                ("https://jobs-b.example",),
                "t2",
            ),
        ],
        outages={("news_events", 12)},
        rate_limited=set(),
    )


@dataclass
class Account:
    tenant: UUID
    account: UUID
    domain: str
    brief_built_at: Any


async def simulate(seed: int | None) -> dict[str, Any]:
    suffix = f"{seed if seed is not None else 'base'}-{random.random():.6f}"[2:]
    w = world(suffix)
    clock = FakeClock()
    accounts: dict[str, Account] = {}
    for name in ("alpha", "beta", "gamma"):
        t, acc = tenant_with_account(f"p4-sim-{name}", f"{name}-{suffix}.example")
        accounts[name] = Account(t, acc, f"{name}-{suffix}.example", EPOCH)
    async with tenant_txn(accounts["alpha"].tenant) as s:
        await subscriptions.save(
            s,
            "alpha-seller",
            subscriptions.Subscription(
                immediate_rules=frozenset(
                    r for r, v in load_rules().items() if v.tier == "immediate"
                )
            ),
            set_by="alpha-seller",
            bulk=False,
            now=EPOCH,
        )
    beta_monitors = {k: FakeMonitor(k, w, clock) for k in KINDS}
    w.rate_limited = set()
    rng = random.Random(seed)
    escalations: list[tuple[str, int]] = []
    removals: list[tuple[str, int, str]] = []
    blocked: Counter[tuple[str, str]] = Counter()
    no_change: Counter[str] = Counter()
    for week in range(WEEKS):  # LOOP(simulation weeks): cap=26; exit=end
        clock.current = EPOCH + timedelta(weeks=week)
        order = list(accounts)
        if seed is not None:
            rng.shuffle(order)
        for name in order:
            a = accounts[name]
            monitors = (
                beta_monitors if name == "beta" else {k: FakeMonitor(k, w, clock) for k in KINDS}
            )
            if name == "beta":
                w.rate_limited = {"leadership_changes"}
            period = schedule.period_key("weekly", clock.current)
            async with tenant_txn(a.tenant) as s:
                await slots.ensure(
                    s, account_id=a.account, kind="probe", period=period, due_at=clock.current
                )
                await slots.ensure(
                    s, account_id=a.account, kind="probe", period=period, due_at=clock.current
                )  # a duplicate tick
                claimed = await slots.claim(s, now=clock.current, worker=f"w-{name}", limit=5)
            probe_slots = [c for c in claimed if c.kind == "probe"]
            assert len(probe_slots) == 1
            deps = probe.Deps(
                monitors,
                FakeGrader(),
                ["alpha-seller", "alpha-viewer"] if name == "alpha" else [f"{name}-user"],
                "https://app.test",
                "pilot",
                1,
                CFG["delta_weights"],
                RULES,
                2,
                0.8,
                0.5,
                7,
            )
            res = await probe.run_probe(
                tenant_txn,
                a.tenant,
                account_id=a.account,
                domain=a.domain,
                kinds=KINDS,
                brief_built_at=a.brief_built_at,
                priority_a=False,
                account_score=10,
                slot_id=probe_slots[0].id,
                deps=deps,
                now=clock.current,
            )
            w.rate_limited = set()
            async with tenant_txn(a.tenant) as s:
                await slots.finish(
                    s, probe_slots[0].id, status="done", outcome="probed", now=clock.current
                )
            for row, outcome in res.blocked.items():
                blocked[(name, f"{row}:{outcome}")] += 1
            for key in res.removed:
                removals.append((name, week, key))
            if res.no_change:
                no_change[name] += 1
            if res.escalated:
                escalations.append((name, week))
                # the escalated refresh: inherits the probe's rows, rebuilds the brief
                async with tenant_txn(a.tenant) as s:
                    refresh_slots = await slots.claim(s, now=clock.current, worker="w", limit=5)
                    assert [x.kind for x in refresh_slots] == ["refresh"]
                    rows = [
                        planner.PriorRow(k, "high", clock.current, str(res.run_id))
                        for k in ("company_news", "job_board_by_lane", "technology_detections")
                    ]
                    plan = planner.plan(
                        "refresh",
                        rows,
                        policy.load(),
                        clock.current,
                        probe_sources=[],
                        inherited={
                            "company_news": str(res.run_id),
                            "job_board_by_lane": str(res.run_id),
                        },
                    )
                    assert [p.action for p in plan] == ["not_due", "not_due", "rerun"]
                    rid = await records.record_run(
                        s,
                        slot_id=refresh_slots[0].id,
                        account_id=a.account,
                        kind="refresh",
                        parent_run_id=None,
                        decision={"escalated_from": str(res.run_id)},
                        now=clock.current,
                    )
                    await records.record_plan(s, rid, plan)
                    await slots.finish(
                        s,
                        refresh_slots[0].id,
                        status="done",
                        outcome="refreshed",
                        now=clock.current,
                    )
                a.brief_built_at = clock.current
    alerts = {}
    for name, a in accounts.items():
        alerts[name] = sorted(
            (int((r[0] - EPOCH).days // 7), r[1], r[2], r[3], r[4])
            for r in db(
                "SELECT a.created_at, a.rule_id, a.tier, a.lead,"
                " (SELECT count(*) FROM signal_event_evidence x WHERE x.event_id = a.event_id)"
                " FROM alerts a WHERE a.tenant_id = %s",
                a.tenant,
            )
        )
    channels = {
        name: sorted(
            db("SELECT o.recipient, o.channel FROM alert_outbox o WHERE o.tenant_id = %s", a.tenant)
        )
        for name, a in accounts.items()
    }
    history = {
        name: db(
            "SELECT reason, count(*) FROM signal_history WHERE tenant_id = %s GROUP BY reason",
            a.tenant,
        )
        for name, a in accounts.items()
    }
    drops = {
        name: db(
            "SELECT reason, count(*) FROM intake_drops WHERE tenant_id = %s GROUP BY reason",
            a.tenant,
        )
        for name, a in accounts.items()
    }
    probes = {
        name: db(
            "SELECT count(*) FROM refresh_runs WHERE tenant_id = %s AND kind = 'probe'", a.tenant
        )[0][0]
        for name, a in accounts.items()
    }
    return {
        "alerts": alerts,
        "channels": channels,
        "history": history,
        "drops": drops,
        "escalations": sorted(escalations),
        "removals": sorted(removals),
        "blocked": dict(sorted(blocked.items())),
        "no_change": dict(no_change),
        "probes": probes,
    }


# --- the oracle (hand-written) -----------------------------------------------------------------
IMMEDIATE_A = [
    (2, "new_build_role", "immediate", False, 2),  # one event, two outlets as evidence
    (8, "tier_one_news", "immediate", False, 3),  # one event, three outlets
    (14, "new_build_role", "digest", True, 1),  # keyword match: a Lead, digest only
]
ORACLE: dict[str, Any] = {
    "alerts": {
        "alpha": IMMEDIATE_A,
        "beta": [(3, "new_build_role", "immediate", False, 1)],
        "gamma": [],
    },
    "history": {"alpha": [("removed_after_probes", 1)], "beta": [], "gamma": []},
    "drops": {"alpha": [("unverifiable", 1)], "beta": [], "gamma": []},
    # the brief is 91 days old in week 13 for every account; the escalated refresh rebuilds it
    "escalations": [("alpha", 13), ("beta", 13), ("gamma", 13)],
    # the posting vanished in week 10 (absent once) and is REMOVED on the second probe, week 11
    "removals": [("alpha", 11, "post-1")],
    "probes": {"alpha": WEEKS, "beta": WEEKS, "gamma": WEEKS},
}


def expected_blocked() -> dict[tuple[str, str], int]:
    out = {(name, "company_news:blocked"): 1 for name in ("alpha", "beta", "gamma")}  # week 12
    out[("beta", "leadership_changes:blocked")] = WEEKS  # rate limited forever
    return dict(sorted(out.items()))


async def test_a_probe_runs_end_to_end_with_delta_events_and_alerts() -> None:
    got = await simulate(None)
    for key in ("alerts", "history", "drops", "escalations", "removals", "probes"):
        assert got[key] == ORACLE[key], (key, got[key])
    assert got["blocked"] == expected_blocked()
    # subscribe up: the subscribed seller gets email for immediate alerts; the others digest only
    alpha_ch = Counter(c for _, c in got["channels"]["alpha"])
    assert ("alpha-seller", "email") in got["channels"]["alpha"]
    assert ("alpha-viewer", "email") not in got["channels"]["alpha"]
    assert all(c != "email" for _, c in got["channels"]["beta"])
    assert alpha_ch["email"] == 2  # two immediate alerts, the Lead is never emailed
    # gamma's world is quiet: every probe states "no change"
    assert got["no_change"].get("gamma") == WEEKS


async def test_the_simulation_is_identical_under_shuffled_interleavings() -> None:
    base = await simulate(None)
    # 5 by default (each run is 26 weeks x 3 tenants); SIM_INTERLEAVINGS=50 for the full gate
    for seed in range(int(os.environ.get("SIM_INTERLEAVINGS", "5"))):
        again = await simulate(seed)
        for key in (
            "alerts",
            "history",
            "drops",
            "escalations",
            "removals",
            "blocked",
            "no_change",
            "probes",
        ):
            assert again[key] == base[key], (seed, key)
