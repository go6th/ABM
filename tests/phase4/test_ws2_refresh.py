"""WS2 refresh engine (RF1 to RF7). Lying scenarios: a refresh that redoes everything, an expired
signal deleted, a reused source code, an escalation without a reason, a removal on one absence or
during an outage, an omitted empty delta, outreach wording in the Summary."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from alerts import lint
from refresh import debounce, delta, escalate, history, observations, planner, policy, records
from refresh.summary_diff import render
from tests.phase4.conftest import NOW, db, fact, tenant_with_account

CFG = yaml.safe_load((CONFIG_DIR / "refresh.yaml").read_text())
W = CFG["delta_weights"]


def fv(
    ident: str, grade: str | None, sig: str = "news_event", fid: str | None = None
) -> delta.FactView:
    return delta.FactView(fid or f"f-{ident}-{grade}", ident, sig, grade, "S-0001")


# --- delta -------------------------------------------------------------------------------------


def test_golden_deltas_for_every_change_kind() -> None:
    before = [fv("a", "probable"), fv("b", "lead"), fv("c", "confirmed")]
    after = [fv("a", "probable"), fv("b", "confirmed"), fv("d", "probable", "build_role_posting")]
    expired = [fv("c", "confirmed")]
    counts_b = {"open_postings:sap": delta.Count("open_postings:sap", 3, "postings seen 2026-02")}
    counts_a = {"open_postings:sap": delta.Count("open_postings:sap", 5, "postings seen 2026-03")}
    d = delta.compute(
        before, after, expired, counts_b, counts_a, checked=["company_news"], checked_at=NOW
    )
    got = [(i.kind, i.identity, i.before, i.after, i.direction) for i in d.items]
    assert got == [
        ("regraded", "b", "lead", "confirmed", "up"),
        ("new", "d", None, "probable", None),
        ("expired", "c", "confirmed", None, None),
        ("count_change", "open_postings:sap", "3", "5", None),
    ]
    assert d.items[-1].base == "postings seen 2026-03" and not d.no_change
    assert (
        delta.score(d, W)
        == W["regraded_up"] + W["new"]["build_role_posting"] + W["expired"] + W["count_change"]
    )
    down = delta.compute(
        [fv("x", "confirmed")], [fv("x", "hypothesis")], [], {}, {}, checked=[], checked_at=NOW
    )
    assert down.items[0].direction == "down" and delta.score(down, W) == W["regraded_down"]
    empty = delta.compute(
        after, after, [], counts_a, counts_a, checked=["company_news"], checked_at=NOW
    )
    assert empty.no_change and empty.items == () and empty.checked == ("company_news",)
    other = delta.compute(
        [], [fv("z", "lead", "unlisted_type")], [], {}, {}, checked=[], checked_at=NOW
    )
    assert delta.score(other, W) == W["new"]["other"]


@settings(max_examples=80, deadline=None)
@given(
    st.dictionaries(st.sampled_from("abcdefgh"), st.sampled_from(delta.GRADE_ORDER), max_size=8),
    st.dictionaries(st.sampled_from("abcdefgh"), st.sampled_from(delta.GRADE_ORDER), max_size=8),
)
def test_a_delta_is_deterministic_and_reconstructs_the_after_set(
    old: dict[str, str], new: dict[str, str]
) -> None:
    before = [fv(k, g) for k, g in old.items()]
    after = [fv(k, g) for k, g in new.items()]
    expired = [fv(k, g) for k, g in old.items() if k not in new]
    d1 = delta.compute(before, after, expired, {}, {}, checked=[], checked_at=NOW)
    d2 = delta.compute(
        list(reversed(before)), list(reversed(after)), expired, {}, {}, checked=[], checked_at=NOW
    )
    assert d1 == d2
    state = dict(old)
    for it in d1.items:  # replaying the delta on the old state gives the new state
        if it.kind == "expired":
            state.pop(it.identity)
        else:
            state[it.identity] = it.after  # type: ignore[assignment]
    assert state == new


# --- escalation --------------------------------------------------------------------------------


R = escalate.Rules(threshold=10, max_brief_age_days=90, min_interval_days=7, max_per_period=2)


@pytest.mark.parametrize(
    ("score", "age", "last", "n", "expected"),
    [
        (12, 10, None, 0, (True, "delta_score")),
        (3, 95, None, 0, (True, "brief_age")),
        (3, 10, None, 0, (False, "below_threshold")),
        (12, 10, 3, 0, (False, "min_interval")),
        (12, 10, 8, 0, (True, "delta_score")),
        (12, 10, None, 2, (False, "period_cap")),
    ],
)
def test_the_escalation_decision_is_pure_and_capped(
    score: int, age: int, last: int | None, n: int, expected: tuple[bool, str]
) -> None:
    d = escalate.decide(
        now=NOW,
        delta_score=score,
        brief_built_at=NOW - timedelta(days=age),
        last_escalation_at=None if last is None else NOW - timedelta(days=last),
        escalations_in_period=n,
        rules=R,
    )
    assert (d.escalate, d.reason) == expected
    assert (d.delta_score, d.brief_age_days, d.escalations_in_period) == (score, age, n)


# --- debounce ----------------------------------------------------------------------------------


def run_obs(seq: list[debounce.Observation]) -> list[str]:
    t, out = debounce.Tracked("present", 0), []
    for o in seq:
        t, e = debounce.observe(t, o, absences_to_remove=2)
        out.append(e)
    return out


def test_removals_need_consecutive_absences_and_outages_do_not_count() -> None:
    assert run_obs(["absent"]) == ["pending_removal"]
    assert run_obs(["absent", "absent"]) == ["pending_removal", "removed"]
    assert run_obs(["absent", "present"]) == ["pending_removal", "removal_cancelled"]
    assert run_obs(["absent", "blocked", "blocked"]) == ["pending_removal", "none", "none"]
    assert run_obs(["blocked", "absent", "blocked", "absent"])[-1] == "removed"
    assert run_obs(["absent", "absent", "absent", "present"])[-2:] == ["none", "reappeared"]
    with pytest.raises(ValueError):
        debounce.observe(debounce.Tracked("present", 0), "absent", absences_to_remove=0)


# --- planner -----------------------------------------------------------------------------------


def test_the_plan_reruns_only_due_rows() -> None:
    p = policy.load()
    rows = [
        planner.PriorRow("company_news", "high", NOW - timedelta(days=2), "r1"),
        planner.PriorRow("job_board_by_lane", "high", NOW - timedelta(days=9), "r1"),
        planner.PriorRow("podcasts", "medium", NOW - timedelta(days=40), "r1"),
        planner.PriorRow("review_sites", "medium", NOW - timedelta(days=40), "r1"),
        planner.PriorRow("vendor_customer_stories", "high", None, "r1"),
        planner.PriorRow("research_papers", "optional", None, "r1"),
    ]
    probe = {
        r.row_key: (r.action, r.reason)
        for r in planner.plan("probe", rows, p, NOW, probe_sources=["job_postings", "news_events"])
    }
    assert probe == {
        "company_news": ("not_due", "not_due"),
        "job_board_by_lane": ("rerun", "probe_source"),
        "podcasts": ("not_due", "not_due"),
        "review_sites": ("not_due", "not_due"),
        "vendor_customer_stories": ("not_due", "no_cadence"),
        "research_papers": ("not_due", "no_cadence"),
    }
    full = {
        r.row_key: (r.action, r.reason, r.carried_from_run)
        for r in planner.plan("refresh", rows, p, NOW, probe_sources=[])
    }
    assert full == {
        "company_news": ("rerun", "high_priority", None),  # PRD: High rows re-run
        "job_board_by_lane": ("rerun", "shelf_life", None),
        "podcasts": ("rerun", "shelf_life", None),
        "review_sites": ("not_due", "not_due", "r1"),  # quarterly, checked 40 days ago
        "vendor_customer_stories": ("rerun", "high_priority", None),
        "research_papers": ("not_due", "no_cadence", "r1"),
    }
    longer = planner.plan(
        "refresh", rows[2:3], p, NOW, probe_sources=[], overrides={"podcasts": 60}
    )
    assert longer[0].action == "not_due"  # a tenant override lengthened the shelf life


# --- Summary diff ------------------------------------------------------------------------------


def test_the_summary_opens_with_the_rendered_delta() -> None:
    d = delta.compute(
        [fv("a", "lead")],
        [fv("a", "probable"), fv("brk", "confirmed")],
        [],
        {},
        {},
        checked=["company_news"],
        checked_at=NOW,
    )
    text = render(
        d,
        since_version=3,
        since_date=date(2026, 2, 1),
        labels={"a": "Hiring a platform owner"},
        event_types={"brk": "data_breach"},
    )
    assert text.startswith("What changed since version 3 (2026-02-01)")
    assert "Hiring a platform owner: lead to probable [S-0001]" in text
    assert "Preparation only, not an opening line." in text and "brk" not in text
    empty = delta.compute(
        [], [], [], {}, {}, checked=["company_news", "job_board_by_lane"], checked_at=NOW
    )
    e = render(empty, since_version=3, since_date=date(2026, 2, 1))
    assert "No change. Checked: company_news, job_board_by_lane, on 2026-03-02." in e
    with pytest.raises(lint.WordingRefused):
        render(
            d,
            since_version=3,
            since_date=date(2026, 2, 1),
            labels={"a": "Great time to reach out about hiring"},
        )


# --- database ----------------------------------------------------------------------------------


async def _refresh_run(t: UUID, a: UUID) -> UUID:
    async with tenant_txn(t) as s:
        return await records.record_run(
            s,
            slot_id=None,
            account_id=a,
            kind="probe",
            parent_run_id=None,
            decision={"reason": "test"},
            now=NOW,
        )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_expired_signals_move_to_history_and_are_never_deleted() -> None:
    t, a = tenant_with_account("p4-history")
    f = fact(t, a)
    run = await _refresh_run(t, a)
    async with tenant_txn(t) as s:
        assert await history.expire(
            s, fact_id=f, signal_type="news_event", reason="shelf_life", run_id=run, now=NOW
        )
        assert not await history.expire(
            s, fact_id=f, signal_type="news_event", reason="shelf_life", run_id=run, now=NOW
        )
        with pytest.raises(ValueError):
            await history.expire(
                s, fact_id=f, signal_type="x", reason="deleted", run_id=run, now=NOW
            )
        listed = await history.listing(s, a)
    assert [h["reason"] for h in listed] == ["shelf_life"]
    assert db("SELECT superseded_at IS NOT NULL FROM facts WHERE id = %s", f) == [(True,)]
    current = db("SELECT count(*) FROM facts WHERE account_id = %s AND superseded_at IS NULL", a)
    assert current == [(0,)]  # out of current counts, still in the table
    import psycopg

    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(
            "SELECT set_config('app.tenant_id', %s, false),"
            " set_config('app.scope', 'system', false)",
            (str(t),),
        )
        c.execute("DELETE FROM signal_history WHERE fact_id = %s", (f,))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_refresh_records_its_decision_plan_and_delta() -> None:
    t, a = tenant_with_account("p4-records")
    f = fact(t, a)
    run = await _refresh_run(t, a)
    d = delta.compute(
        [], [fv("x", "probable", fid=str(f))], [], {}, {}, checked=["company_news"], checked_at=NOW
    )
    plan = planner.plan(
        "probe",
        [planner.PriorRow("company_news", "high", None, str(uuid4()))],
        policy.load(),
        NOW,
        probe_sources=["news_events"],
    )
    async with tenant_txn(t) as s:
        await records.record_plan(s, run, plan)
        did, items = await records.record_delta(s, run_id=run, account_id=a, delta=d, score=2)
    assert len(items) == 1
    assert db("SELECT no_change, score FROM deltas WHERE id = %s", did) == [(False, 2)]
    assert db("SELECT action, reason FROM refresh_row_plans WHERE refresh_run_id = %s", run) == [
        ("rerun", "probe_source")
    ]
    empty = delta.compute([], [], [], {}, {}, checked=["company_news"], checked_at=NOW)
    run2 = await _refresh_run(t, a)
    async with tenant_txn(t) as s:
        did2, _ = await records.record_delta(s, run_id=run2, account_id=a, delta=empty, score=0)
    assert db("SELECT no_change, checked FROM deltas WHERE id = %s", did2) == [
        (True, ["company_news"])
    ]  # an empty delta is stated, never omitted


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_debounce_observations_persist_across_probes() -> None:
    t, a = tenant_with_account("p4-debounce")
    events = []
    for seen in ("absent", "blocked", "absent", "present"):
        run = await _refresh_run(t, a)
        async with tenant_txn(t) as s:
            events.append(
                await observations.record(
                    s,
                    account_id=a,
                    item_key="posting:123",
                    probe_run_id=run,
                    seen=seen,  # type: ignore[arg-type]
                    absences_to_remove=2,
                    now=NOW,
                )
            )
    assert events == ["pending_removal", "none", "removed", "reappeared"]


@pytest.mark.requires_postgres
@settings(max_examples=15, deadline=None)
@given(
    st.lists(
        st.lists(
            st.sampled_from([f"https://news.example/{i}" for i in range(12)]),
            min_size=1,
            max_size=6,
        ),
        min_size=2,
        max_size=6,
    )
)
def test_source_codes_are_stable_across_refresh_cycles(cycles: list[list[str]]) -> None:
    import os

    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from account_intelligence.db.enums import SourceKind
    from account_intelligence.db.session import make_sync_url
    from refresh.sourcecodes import resolve
    from tests.conftest import ADMIN_DSN
    from tests.db.conftest import make_account, make_run, make_tenant, set_tenant

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tenant = make_tenant(admin)
        account = make_account(admin, tenant)
        runs = [make_run(admin, tenant, account) for _ in cycles]
    engine = create_engine(make_sync_url(os.environ["DATABASE_URL"]))
    seen: dict[str, str] = {}
    try:
        for run, urls in zip(runs, cycles, strict=True):
            with Session(engine) as session, session.begin():
                set_tenant(session, tenant)
                for url in urls:
                    code = resolve(
                        session, account_id=account, run_id=run, url=url, kind=SourceKind.PRIMARY
                    )
                    assert seen.setdefault(url, code) == code  # stable
        assert len(set(seen.values())) == len(seen)  # never reused for another source
    finally:
        engine.dispose()
