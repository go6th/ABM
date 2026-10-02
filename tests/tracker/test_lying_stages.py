"""Lying-stage tests (spec 9.3): every T-rule, on real Postgres through the production adapters.

Each refused write must raise its typed error and leave rows and events unchanged
(``_snapshot`` before and after). Synthetic data only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from account_intelligence.tracker.errors import (
    IllegalTransition,
    NotOwner,
    ProofIncomplete,
    ProofMismatch,
    TrackerError,
    UnsafeDetail,
)
from account_intelligence.tracker.model import Actor, ReasonCode, Status
from tests.tracker.world import World, fetch, ledger_done, ledger_failed, make_world, txn

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

NEWS = "company_news"  # owned by stage "news"
JOBS = "job_board_by_lane"  # owned by stage "jobs"


def _broker() -> str:
    """A real broker name, read from the one module allowed to hold it."""
    from account_intelligence.connectors.base import ProviderCode
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    return vendor_names(load_providers_config())[ProviderCode.P_SCRAPE]


async def _seeded() -> World:
    w = make_world()
    async with txn(w) as t:
        await t.seed(w.run, w.tenant)
    return w


def _snapshot(w: World) -> tuple[Any, Any]:
    rows = fetch(
        "SELECT row_key, status, proof_hash, version, attempts, swept, detail FROM coverage_rows"
        " WHERE run_id = %s ORDER BY row_key",
        w.run,
    )
    events = fetch("SELECT id FROM coverage_row_events WHERE run_id = %s ORDER BY id", w.run)
    return rows, events


async def _refused(w: World, error: type[TrackerError], call: Callable[..., Any]) -> None:
    before = _snapshot(w)
    with pytest.raises(error):
        async with txn(w) as t:
            await call(t)
    assert _snapshot(w) == before


async def _record(w: World, t: Any, row: str, refs: list[str], **kw: Any) -> Any:
    kw.setdefault("stage", "news" if row == NEWS else "jobs")
    return await t.record_outcome(w.tenant, w.run, row, refs, kw.pop("actor", Actor.STAGE), **kw)


async def test_t1_done_with_no_calls_is_refused() -> None:
    w = await _seeded()
    await _refused(w, ProofIncomplete, lambda t: _record(w, t, NEWS, []))


async def test_t2_a_call_ref_never_made_is_refused() -> None:
    w = await _seeded()
    await _refused(w, ProofMismatch, lambda t: _record(w, t, NEWS, ["f" * 64]))


async def test_t2_another_run_another_tenant_and_a_previous_run_are_refused() -> None:
    w = await _seeded()
    other_run = make_world(tenant=w.tenant)  # same tenant, previous or parallel run
    ref_other_run = ledger_done(other_run)
    other_tenant = make_world()
    ref_other_tenant = ledger_done(other_tenant)
    for ref in (ref_other_run, ref_other_tenant):
        await _refused(w, ProofMismatch, lambda t, r=ref: _record(w, t, NEWS, [r]))


async def test_t2_inflated_count_is_impossible_counts_come_from_the_ledger() -> None:
    w = await _seeded()
    ref = ledger_done(w, record_count=0)
    async with txn(w) as t:
        result = await _record(w, t, NEWS, [ref])
    assert result.status is Status.NONE  # the ledger says zero; no parameter can say otherwise
    (proof,) = fetch(
        "SELECT proof FROM coverage_rows WHERE run_id = %s AND row_key = %s", w.run, NEWS
    )
    assert proof[0]["calls"][0]["result_count"] == 0


async def test_t2_unknown_tool_category_is_refused() -> None:
    w = await _seeded()
    ref = ledger_done(w, operation="made_up_operation")
    await _refused(w, ProofMismatch, lambda t: _record(w, t, NEWS, [ref]))


async def test_t3_unknown_count_supports_only_partial() -> None:
    w = await _seeded()
    ref = ledger_done(w, record_count=None)
    async with txn(w) as t:
        result = await _record(w, t, NEWS, [ref])
    assert (result.status, result.reason_code) == (
        Status.PARTIAL,
        ReasonCode.RESULT_COUNT_UNKNOWN,
    )


async def test_t4_none_while_the_ledger_shows_results_is_impossible() -> None:
    w = await _seeded()
    ref = ledger_done(w, record_count=4)
    async with txn(w) as t:
        result = await _record(w, t, NEWS, [ref])
    assert result.status is Status.DONE


async def test_t6_blocked_needs_a_failure_and_not_done_cannot_hide_one() -> None:
    w = await _seeded()
    failed = ledger_failed(w, error="RateLimited")
    async with txn(w) as t:
        result = await _record(w, t, NEWS, [failed], not_attempted_reason=ReasonCode.DEADLINE)
    # a failure on record is Blocked with its reason, never a quiet Not done
    assert (result.status, result.reason_code) == (
        Status.BLOCKED,
        ReasonCode.SOURCE_RATE_LIMITED,
    )


async def test_t6_schema_drift_paid_body_is_a_failure() -> None:
    w = await _seeded()
    ref = ledger_done(w, record_count=None, error_class="SchemaDriftError")
    async with txn(w) as t:
        result = await _record(w, t, NEWS, [ref])
    assert (result.status, result.reason_code) == (Status.BLOCKED, ReasonCode.SOURCE_FAILED)


async def test_t7_a_stage_writing_a_row_it_does_not_own_is_refused() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    await _refused(w, NotOwner, lambda t: _record(w, t, NEWS, [ref], stage="jobs"))
    await _refused(w, NotOwner, lambda t: _record(w, t, NEWS, [ref], stage=None))


@pytest.mark.parametrize("actor", [Actor.JUDGE, Actor.GATE, Actor.FINALIZER])
async def test_t7_judge_gate_and_finalizer_cannot_upgrade(actor: Actor) -> None:
    w = await _seeded()
    ref = ledger_done(w)
    await _refused(w, NotOwner, lambda t: _record(w, t, NEWS, [ref], actor=actor))


async def test_t7_verifier_only_on_its_rows() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    await _refused(
        w, NotOwner, lambda t: _record(w, t, NEWS, [ref], actor=Actor.VERIFIER, stage=None)
    )
    async with txn(w) as t:
        result = await t.record_outcome(w.tenant, w.run, "lead_verification", [ref], Actor.VERIFIER)
    assert result.status is Status.DONE


async def test_t7_gate_cannot_upgrade_through_downgrade() -> None:
    w = await _seeded()
    ref = ledger_done(w, record_count=0, queries=("a",))
    async with txn(w) as t:
        await _record(w, t, JOBS, [ref], expected_calls=2)  # partial: fewer than expected
    await _refused(
        w,
        NotOwner,
        lambda t: t.downgrade(w.tenant, w.run, JOBS, Status.DONE, Actor.GATE, ReasonCode.DEADLINE),
    )


async def test_t8_swapped_proof_is_refused() -> None:
    w = await _seeded()
    first, second = ledger_done(w), ledger_done(w)
    async with txn(w) as t:
        await _record(w, t, NEWS, [first])
    await _refused(w, IllegalTransition, lambda t: _record(w, t, NEWS, [second]))
    async with txn(w) as t:  # extending is fine
        result = await _record(w, t, NEWS, [first, second])
    assert result.status is Status.DONE and not result.unchanged


@pytest.mark.parametrize(
    "detail",
    [
        "the BROKER scrape timed out",  # a broker name (taken from the internal registry)
        "used 40 credits on this row",  # a credit figure
        "same team as other-account.example.com",  # another account's name
    ],
)
async def test_t9_unsafe_detail_is_refused(detail: str) -> None:
    w = await _seeded()
    other = make_world(tenant=w.tenant)
    ref = ledger_done(w)
    detail = detail.replace("other-account.example.com", other.domain).replace("BROKER", _broker())
    await _refused(w, UnsafeDetail, lambda t: _record(w, t, NEWS, [ref], detail=detail))


async def test_t9_safe_detail_is_cleaned_capped_and_internal_only() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    async with txn(w) as t:
        await _record(w, t, NEWS, [ref], detail="feed\x00 returned " + "x" * 900)
        view = await t.view(w.run, "client")
    (detail,) = fetch(
        "SELECT detail FROM coverage_rows WHERE run_id = %s AND row_key = %s", w.run, NEWS
    )
    assert detail[0].startswith("feed returned") and len(detail[0]) == 500
    assert "feed returned" not in str(view)


async def test_t12_swept_row_cannot_be_presented_as_done() -> None:
    w = await _seeded()
    async with txn(w) as t:
        swept = await t.sweep(w.run, w.tenant, ReasonCode.SWEPT_FAILED)
    assert len(swept) == 33
    ref = ledger_done(w)
    await _refused(w, IllegalTransition, lambda t: _record(w, t, NEWS, [ref]))


async def test_t14_downgrade_to_none_or_blocked_is_refused() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    async with txn(w) as t:
        await _record(w, t, NEWS, [ref])
    for target in (Status.NONE, Status.BLOCKED):
        await _refused(
            w,
            IllegalTransition,
            lambda t, s=target: t.downgrade(
                w.tenant, w.run, NEWS, s, Actor.JUDGE, ReasonCode.CLAIMS_DROPPED
            ),
        )
