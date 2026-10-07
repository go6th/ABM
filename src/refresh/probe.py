"""The probe orchestrator (RF1 to RF6, SM1 to SM4, CG1, CG2, AL1): one probe of one account,
end to end, with no transaction held across a provider call.

A. Snapshot the account's current signal facts. For each monitor kind of the account: reserve
   monitoring allowance (an atomic conditional update in its own short transaction; exhaustion
   or a throttled account means the kind is not polled and its rows are Blocked with a reason),
   then poll OUTSIDE any transaction.
B. Hand each observation to the ``Grader`` port (production: the tool-less extractor and the
   grading engine, which records the fact; here a port). An unverifiable candidate is dropped
   with a counted reason, never promoted.
C. In one transaction: debounce every watched item (absent, present, or blocked: an outage never
   counts as absence), expire removed items into history, compute the delta against the snapshot,
   decide escalation (pure, recorded with its inputs), record the run, the observations, the delta,
   the signal events (one per fingerprint; evidence attaches) and evaluate the alert rules on the
   new events (every alert has its delta item).

An escalation is a pending ``refresh`` slot for the month; the planner gives it the probe's rows
as inherited (never pulled twice).

GUARANTEE(test_a_probe_runs_end_to_end_with_delta_events_and_alerts)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text

from alerts import engine
from governance import monitoring_budget, throttle
from refresh import debounce, delta, escalate, history, records, schedule, slots
from refresh import observations as obs_store
from signals import events, fingerprint, intake, score
from signals.registry import ROW_OF, Monitor, MonitorKind, Observation

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[Any]]
SIGNAL_OF: dict[str, str] = {
    "build_role_postings": "build_role_posting",
    "leadership_changes": "leadership_change",
    "news_events": "news_event",
    "tech_detection_changes": "tech_detection",
    "facility_expansions": "facility_expansion",
    "vendor_customer_stories": "vendor_customer_story",
}


@dataclass(frozen=True)
class Graded:
    """What the grading side returns for one observation (the fact is already recorded)."""

    fact_id: UUID
    grade: str
    read_in_this_refresh: bool
    source_code: str | None


class Grader(Protocol):
    """Extractor plus grading engine (production wiring: the workflow's nodes)."""

    async def grade(self, tenant: UUID, account_id: UUID, obs: Observation) -> Graded | None:
        """The recorded fact, or None when the candidate is unverifiable."""


@dataclass
class Deps:
    """Ports and settings."""

    monitors: Mapping[MonitorKind, Monitor]
    grader: Grader
    recipients: Sequence[str]
    app_base: str
    plan: str
    units_per_poll: int
    weights: Mapping[str, Any]
    rules: escalate.Rules
    absences_to_remove: int
    cluster_same: float
    cluster_ambiguous: float
    bucket_days: int


@dataclass
class ProbeResult:
    """What one probe did."""

    run_id: UUID
    delta_score: int
    no_change: bool
    escalated: bool
    escalation_reason: str
    blocked: dict[str, str] = field(default_factory=dict)
    alerts: list[UUID] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    dropped: int = 0


async def _current(
    s: Any, account_id: UUID, *, reported_since: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """Current signal facts. With ``reported_since`` (the brief's build time): only facts the
    account has already been told about: those that existed when the brief was built, or that an
    earlier delta named. A fact recorded by a probe that crashed before its delta stays NEW for
    the retry, so its alert is never silently lost."""
    rows = (
        await s.execute(
            text(
                "SELECT f.id, f.product_grade, f.attribute_key, f.value_json FROM facts f"
                " WHERE f.account_id = :a AND f.superseded_at IS NULL"
                " AND f.attribute_key LIKE 'signal.%' AND (CAST(:since AS timestamptz) IS NULL"
                " OR f.created_at <= CAST(:since AS timestamptz) OR EXISTS (SELECT 1 FROM"
                " delta_items d WHERE d.fact_id = f.id))"
            ),
            {"a": account_id, "since": reported_since},
        )
    ).all()
    return {
        str(r.value_json["item_key"]): {
            "fact_id": r.id,
            "grade": r.product_grade,
            "signal": r.attribute_key.removeprefix("signal."),
            "kind": r.value_json.get("kind"),
        }
        for r in rows
    }


async def _escalation_inputs(
    s: Any, account_id: UUID, now: datetime
) -> tuple[datetime | None, int]:
    rows = (
        await s.execute(
            text(
                "SELECT created_at FROM refresh_runs WHERE account_id = :a AND kind = 'probe'"
                " AND (decision->>'escalate')::boolean ORDER BY created_at DESC"
            ),
            {"a": account_id},
        )
    ).all()
    month = schedule.period_key("monthly", now)
    in_period = sum(1 for r in rows if schedule.period_key("monthly", r.created_at) == month)
    return (rows[0].created_at if rows else None), in_period


async def run_probe(
    txn: TxnFactory,
    tenant: UUID,
    *,
    account_id: UUID,
    domain: str,
    kinds: Sequence[MonitorKind],
    brief_built_at: datetime,
    priority_a: bool,
    account_score: int,
    slot_id: UUID | None,
    deps: Deps,
    now: datetime,
) -> ProbeResult:
    """One probe (see the module docstring)."""
    async with txn(tenant) as s:
        before = await _current(s, account_id, reported_since=brief_built_at)
        used_pct = (await monitoring_budget.tenant_view(s, deps.plan, now))["percent"]
    mode = throttle.plan(
        used_pct,
        [throttle.Account(str(account_id), account_score, priority_a)],
        monitoring_budget.config()["ladder"],
    )[str(account_id)]
    polled: dict[MonitorKind, tuple[str, tuple[Observation, ...]]] = {}
    for kind in kinds:  # LOOP(probe monitors): cap=6 kinds; exit=end
        throttled = mode in ("paused", "stopped")
        units = deps.units_per_poll
        if not throttled:
            try:
                async with txn(tenant) as s:
                    await monitoring_budget.reserve(s, units=units, plan=deps.plan, now=now)
            except monitoring_budget.BudgetExhausted:
                throttled = True
        async with txn(tenant) as s:  # recorded outcome; the poll itself is a port call
            res = await intake.run_monitor(
                s,
                deps.monitors[kind],
                account_id=account_id,
                domain=domain,
                since=None,
                refresh_run_id=None,
                units=units,
                throttled=throttled,
                now=now,
            )
        polled[kind] = (res.outcome, res.observations)
    graded: dict[str, tuple[Observation, Graded]] = {}
    dropped = 0
    for kind, (outcome, obs) in polled.items():
        if outcome != "ok":
            continue
        for o in obs:  # LOOP(grading hand-off): cap=observations of this poll; exit=end
            g = await deps.grader.grade(tenant, account_id, o)
            if g is None:
                dropped += 1
                async with txn(tenant) as s:
                    await intake.drop(
                        s, account_id=account_id, kind=kind, reason="unverifiable", now=now
                    )
                continue
            graded.setdefault(o.item_key, (o, g))
    async with txn(tenant) as s:
        after_now = await _current(s, account_id)  # includes facts recorded by the grader
        prev_states: dict[str, debounce.Tracked] = {}
        for key in before:
            last = (
                await s.execute(
                    text(
                        "SELECT state_after, absences FROM missing_observations WHERE account_id"
                        " = :a"
                        " AND item_key = :k ORDER BY id DESC LIMIT 1"
                    ),
                    {"a": account_id, "k": key},
                )
            ).first()
            prev_states[key] = (
                debounce.Tracked(last.state_after, int(last.absences))
                if last
                else debounce.Tracked("present", 0)
            )
        observations: dict[str, tuple[debounce.Observation, debounce.Tracked]] = {}
        removed: list[str] = []
        for key, info in sorted(before.items()):
            kind = info["kind"]
            outcome = polled.get(kind, ("not_polled", ()))[0]
            seen: debounce.Observation
            if outcome != "ok":
                seen = "blocked"
            else:
                seen = "present" if any(o.item_key == key for o in polled[kind][1]) else "absent"
            nxt, ev = debounce.observe(
                prev_states[key], seen, absences_to_remove=deps.absences_to_remove
            )
            observations[key] = (seen, nxt)
            if ev == "removed":
                removed.append(key)
        snapshot = [
            delta.FactView(str(v["fact_id"]), k, v["signal"], v["grade"], None)
            for k, v in before.items()
        ]
        expired_views = [f for f in snapshot if f.identity in removed]
        after_views = [
            delta.FactView(
                str(v["fact_id"]),
                k,
                v["signal"],
                v["grade"],
                graded[k][1].source_code if k in graded else None,
            )
            for k, v in after_now.items()
            if k not in removed
        ]
        checked = sorted(ROW_OF[k] for k, (o, _) in polled.items() if o == "ok")
        d = delta.compute(
            snapshot, after_views, expired_views, {}, {}, checked=checked, checked_at=now
        )
        d_score = delta.score(d, deps.weights)
        last_esc, n_esc = await _escalation_inputs(s, account_id, now)
        decision = escalate.decide(
            now=now,
            delta_score=d_score,
            brief_built_at=brief_built_at,
            last_escalation_at=last_esc,
            escalations_in_period=n_esc,
            rules=deps.rules,
        )
        blocked = {ROW_OF[k]: o for k, (o, _) in polled.items() if o != "ok"}
        run_id = await records.record_run(
            s,
            slot_id=slot_id,
            account_id=account_id,
            kind="probe",
            parent_run_id=None,
            decision={
                "escalate": decision.escalate,
                "reason": decision.reason,
                "delta_score": decision.delta_score,
                "brief_age_days": decision.brief_age_days,
                "escalations_in_period": decision.escalations_in_period,
                "blocked": blocked,
                "mode": mode,
            },
            now=now,
        )
        for key, (seen, nxt) in sorted(observations.items()):
            await obs_store.insert(
                s,
                account_id=account_id,
                item_key=key,
                probe_run_id=run_id,
                seen=seen,
                state=nxt,
                now=now,
            )
        for key in removed:
            await history.expire(
                s,
                fact_id=before[key]["fact_id"],
                signal_type=before[key]["signal"],
                reason="removed_after_probes",
                run_id=run_id,
                now=now,
            )
        _, item_ids = await records.record_delta(
            s, run_id=run_id, account_id=account_id, delta=d, score=d_score
        )
        result = ProbeResult(
            run_id,
            d_score,
            d.no_change,
            decision.escalate,
            decision.reason,
            blocked,
            removed=removed,
            dropped=dropped,
        )
        for item, item_id in zip(d.items, item_ids, strict=True):
            if item.kind != "new" or item.identity not in graded:
                continue
            o, g = graded[item.identity]
            signal = SIGNAL_OF[o.kind]
            fp = fingerprint.fingerprint(
                signal,
                o.entity,
                {"item": o.item_key},
                o.observed_on.date(),
                bucket_days=deps.bucket_days,
            )
            sc = score.score(
                score.Inputs(
                    signal, g.grade, o.source_tier, o.observed_on.date(), True, priority_a
                ),
                now.date(),
            )
            first: events.Recorded | None = None
            outlets = sorted(
                (x for x in polled[o.kind][1] if x.item_key == o.item_key),
                key=lambda x: x.source_url,
            )
            for ob in outlets:  # LOOP(event evidence): cap=outlets of one item; exit=end
                rec = await events.record(
                    s,
                    events.EventInput(
                        account_id,
                        fp,
                        signal,
                        o.event_type,
                        o.entity,
                        o.title,
                        fingerprint.date_bucket(o.observed_on.date(), deps.bucket_days),
                        g.grade,
                        g.grade == "lead",
                        False,
                        g.fact_id,
                        item_id,
                        sc,
                        ob.source_url,
                        g.source_code,
                        ob.source_tier,
                        "poll",
                        now,
                    ),
                    same=deps.cluster_same,
                    ambiguous=deps.cluster_ambiguous,
                )
                first = first or rec
            if first is not None and first.outcome in ("created", "linked"):
                out = await engine.evaluate(
                    s,
                    first.event_id,
                    recipients=deps.recipients,
                    account_label=domain,
                    read_in_this_refresh=g.read_in_this_refresh,
                    app_base=deps.app_base,
                    now=now,
                )
                result.alerts.extend(out.alerts)
        from obs.setup import record

        record(
            "refresh_probes_total",
            1,
            {"outcome": "blocked" if blocked else ("no_change" if d.no_change else "changed")},
        )
        record("refresh_escalations_total", 1, {"reason": decision.reason})
        if decision.escalate:
            await slots.ensure(
                s,
                account_id=account_id,
                kind="refresh",
                period=schedule.period_key("monthly", now),
                due_at=now,
            )
    return result
