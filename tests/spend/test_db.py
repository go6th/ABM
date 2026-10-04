"""Credit cap on real Postgres (spec 9.4, 9.6, 9.8, 9.9): proposal and approval binding, races,
extensions, K1, one report per terminal state, pre-pulled files, the nightly pass, insert-only
grants, the CHECK and the tenant-view role."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from account_intelligence.db.session import SessionLocal, tenant_txn
from spend import service
from spend.config import SpendConfig
from spend.errors import ApprovalInvalid, CapNotApproved, ExtensionDenied, ProposalStale
from spend.jobs import spend_invariants
from spend.models import Principal
from tests.fakes.spend import COSTS, PLAN, STAGES, FakeBalances, FakeNotifier
from tests.spend.conftest import NOW, admin, capped_run

pytestmark = [pytest.mark.requires_postgres]
OK = {"p_search": "ok", "p_signals": "ok", "p_people": "ok"}


def human(tenant: UUID, role: str = "admin") -> Principal:
    return Principal(kind="human", subject="user-1", tenant_id=str(tenant), role=role)


async def _proposed(tenant: UUID, cfg: SpendConfig, **kw: object) -> tuple[UUID, service.Proposed, FakeNotifier]:
    _, run, _ = capped_run(tenant, **kw)  # type: ignore[arg-type]
    notes = FakeNotifier()
    p = await service.propose(tenant_txn, tenant, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES, notes)
    return run, p, notes


def _as_app_rw(tenant: UUID, sql: str, *params: object) -> list[tuple[object, ...]]:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.mark.asyncio
async def test_propose_store_reuse_and_approve(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, notes = await _proposed(tenant_a, cfg)
    assert (p.version, p.reused) == (1, False) and notes.prompts == [(str(run), "cap_approval", 1)]
    again = await service.propose(tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    assert (again.version, again.reused) == (1, True)
    snaps = admin("SELECT provider_code, status FROM balance_snapshots WHERE run_id = %s ORDER BY 1", run)
    assert snaps == [("p_people", "ok"), ("p_search", "ok"), ("p_signals", "ok")]
    assert admin("SELECT cap_version FROM account_runs WHERE id = %s", run) == [(None,)]  # not yet
    cap = await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    assert cap == p.proposal.cap_micros
    row = admin("SELECT cap_version, budget_usd, cap_sample_ops FROM account_runs WHERE id = %s", run)[0]
    assert row[0] == 1 and row[2] == {"p_people:profile_enrichment": cfg.sample_size}
    events = admin("SELECT version, event, principal FROM run_cap_events WHERE run_id = %s ORDER BY id", run)
    assert events == [(1, "proposed", None), (1, "approved", "human:user-1")]
    with pytest.raises(ApprovalInvalid):  # a replay is refused (single use)
        await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    reused = await service.propose(tenant_txn, tenant_a, run, PLAN, COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    assert reused.reused and reused.version == 1  # an approved cap is never re-proposed


@pytest.mark.asyncio
async def test_approval_binding_on_the_database(tenant_a: UUID, tenant_b: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    digest = p.proposal.content_hash
    with pytest.raises(ProposalStale):
        await service.approve(tenant_txn, tenant_a, run, 1, "f" * 64, human(tenant_a), cfg, NOW)
    with pytest.raises(ApprovalInvalid):  # another tenant: the run is invisible under RLS
        await service.approve(tenant_txn, tenant_b, run, 1, digest, human(tenant_b), cfg, NOW)
    with pytest.raises(ApprovalInvalid):  # a principal of another tenant on this tenant's run
        await service.approve(tenant_txn, tenant_a, run, 1, digest, human(tenant_b), cfg, NOW)
    with pytest.raises(ApprovalInvalid):
        agent = Principal(kind="agent", subject="planner", tenant_id=str(tenant_a))
        await service.approve(tenant_txn, tenant_a, run, 1, digest, agent, cfg, NOW)
    with pytest.raises(ApprovalInvalid):
        await service.approve(tenant_txn, tenant_a, run, 1, digest, human(tenant_a, "viewer"), cfg, NOW)
    with pytest.raises(ProposalStale):  # past the snapshot age
        await service.approve(tenant_txn, tenant_a, run, 1, digest, human(tenant_a), cfg,
                              NOW + timedelta(minutes=cfg.max_snapshot_age_minutes + 1))
    with pytest.raises(ApprovalInvalid):
        await service.approve(tenant_txn, tenant_a, run, 9, digest, human(tenant_a), cfg, NOW)
    assert admin("SELECT cap_version FROM account_runs WHERE id = %s", run) == [(None,)]


@pytest.mark.asyncio
async def test_double_approval_and_approve_versus_expire_race_decide_once(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    digest = p.proposal.content_hash

    async def attempt() -> str:
        try:
            await service.approve(tenant_txn, tenant_a, run, 1, digest, human(tenant_a), cfg, NOW)
            return "approved"
        except ApprovalInvalid:
            return "refused"

    results = await asyncio.gather(*(attempt() for _ in range(6)))
    assert sorted(results) == ["approved"] + ["refused"] * 5
    run2, p2, _ = await _proposed(tenant_a, cfg)
    late = NOW + timedelta(minutes=cfg.approval_ttl_minutes + 1)

    async def expire() -> list[int]:
        return await service.expire_pending(tenant_txn, tenant_a, run2, cfg, late)

    async def approve() -> str:
        try:
            await service.approve(tenant_txn, tenant_a, run2, 1, p2.proposal.content_hash, human(tenant_a), cfg, NOW)
            return "approved"
        except (ApprovalInvalid, ProposalStale):
            return "refused"

    expired, approved = await asyncio.gather(expire(), approve())
    decisions = admin("SELECT event FROM run_cap_events WHERE run_id = %s AND event IN ('approved', 'expired')", run2)
    assert len(decisions) == 1  # exactly one decision, whichever won
    assert (expired == [1]) != (approved == "approved")


@pytest.mark.asyncio
async def test_reject_and_reproposal(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    with pytest.raises(ApprovalInvalid):
        policy = Principal(kind="tenant_policy", subject="x", tenant_id=str(tenant_a))
        await service.reject(tenant_txn, tenant_a, run, 1, policy, cfg, "no")
    await service.reject(tenant_txn, tenant_a, run, 1, human(tenant_a), cfg, "too high")
    with pytest.raises(ApprovalInvalid):
        await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    second = await service.propose(tenant_txn, tenant_a, run, PLAN[:2], COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    assert second.version == 2 and not second.reused
    third = await service.propose(tenant_txn, tenant_a, run, PLAN[:1], COSTS, FakeBalances(OK, NOW), cfg, NOW, STAGES)
    assert third.version == 3  # the undecided version 2 is expired, never left open
    assert admin("SELECT event FROM run_cap_events WHERE run_id = %s AND version = 2 ORDER BY id", run) == [
        ("proposed",), ("expired",)
    ]
    with pytest.raises(ApprovalInvalid):
        await service.reject(tenant_txn, tenant_a, UUID(int=1), 1, human(tenant_a), cfg, "x")


@pytest.mark.asyncio
async def test_tenant_policy_auto_approval_limits(tenant_a: UUID, cfg: SpendConfig) -> None:
    policy = Principal(kind="tenant_policy", subject="auto_approve_within_reserve", tenant_id=str(tenant_a))
    run, p, _ = await _proposed(tenant_a, cfg, budget="0.05")  # created budget below the cap
    with pytest.raises(ApprovalInvalid, match="limit"):
        await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, policy, cfg, NOW)
    run2, p2, _ = await _proposed(tenant_a, cfg)
    assert await service.approve(tenant_txn, tenant_a, run2, 1, p2.proposal.content_hash, policy, cfg, NOW) > 0
    assert admin("SELECT principal FROM run_cap_events WHERE run_id = %s AND event = 'approved'", run2) == [
        ("tenant_policy:auto_approve_within_reserve",)
    ]


@pytest.mark.asyncio
async def test_extensions_attended_only_higher_bounded_and_approved(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    with pytest.raises(ExtensionDenied, match="unattended"):
        await service.request_extension(tenant_txn, tenant_a, run, p.proposal.cap_micros * 2, "more", cfg)
    arun, ap, _ = await _proposed(tenant_a, cfg, attended=True)
    with pytest.raises(ExtensionDenied, match="no approved cap"):
        await service.request_extension(tenant_txn, tenant_a, arun, 10_000_000, "more", cfg)
    await service.approve(tenant_txn, tenant_a, arun, 1, ap.proposal.content_hash, human(tenant_a), cfg, NOW)
    with pytest.raises(ExtensionDenied, match="strictly higher"):
        await service.request_extension(tenant_txn, tenant_a, arun, ap.proposal.cap_micros, "same", cfg)
    notes = FakeNotifier()
    v = await service.request_extension(tenant_txn, tenant_a, arun, ap.proposal.cap_micros + 1_000_000, "more", cfg, notes)
    assert v == 2 and notes.prompts == [(str(arun), "cap_extension", 2)]
    with pytest.raises(ExtensionDenied, match="limit"):
        await service.request_extension(tenant_txn, tenant_a, arun, ap.proposal.cap_micros + 2_000_000, "again", cfg)
    digest = admin("SELECT proposal_hash FROM run_cap_versions WHERE run_id = %s AND version = 2", arun)[0][0]
    with pytest.raises(ApprovalInvalid):  # the tenant policy can never approve an extension
        policy = Principal(kind="tenant_policy", subject="x", tenant_id=str(tenant_a))
        await service.approve(tenant_txn, tenant_a, arun, 2, str(digest), policy, cfg, NOW)
    cap = await service.approve(tenant_txn, tenant_a, arun, 2, str(digest), human(tenant_a), cfg, NOW)
    assert cap == ap.proposal.cap_micros + 1_000_000
    events = admin("SELECT version, event FROM run_cap_events WHERE run_id = %s ORDER BY id", arun)
    assert (1, "superseded") in events and (2, "approved") in events
    assert admin("SELECT cap_version FROM account_runs WHERE id = %s", arun) == [(2,)]


@pytest.mark.asyncio
async def test_cap_never_lowered_below_spend_in_the_database(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    admin("UPDATE account_runs SET spent_usd = 4.5 WHERE id = %s", run)
    with pytest.raises(ApprovalInvalid, match="spend"):
        await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    with pytest.raises(errors.CheckViolation):  # the CHECK: spent <= budget + overshoot
        _as_app_rw(tenant_a, "UPDATE account_runs SET spent_usd = 6 WHERE id = %s", run)
    _as_app_rw(tenant_a, "UPDATE account_runs SET spent_usd = 6, cap_overshoot_usd = 1 WHERE id = %s", run)


@pytest.mark.asyncio
async def test_close_run_twice_stores_one_report(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    results = await asyncio.gather(*(service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 60) for _ in range(4)))
    assert {r[2] for r in results} == {1} and {r[0].content_hash for r in results} == {results[0][0].content_hash}
    assert admin("SELECT count(*), max(status) FROM spend_reports WHERE run_id = %s", run) == [(1, "reconciled")]
    report, view, _ = results[0]
    assert report.body["cap_versions"][0]["approved_by"] == "human:user-1"  # type: ignore[index]
    assert view.total_calls == 0 and report.body["totals"]["spent_usd"] == "$0.00"  # type: ignore[index]
    with pytest.raises(CapNotApproved):
        await service.close_run(tenant_txn, tenant_a, UUID(int=7), cfg, NOW, 60)


@pytest.mark.asyncio
async def test_mismatched_counters_stamp_the_report_and_the_nightly_pass_flags_it(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    await service.approve(tenant_txn, tenant_a, run, 1, p.proposal.content_hash, human(tenant_a), cfg, NOW)
    admin("UPDATE account_runs SET spent_usd = 0.10 WHERE id = %s", run)  # no ledger event behind it
    report, _, _ = await service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 60)
    assert report.status == "reconciliation_failed"
    assert admin("SELECT status FROM spend_reports WHERE run_id = %s", run) == [("reconciliation_failed",)]


@pytest.mark.asyncio
async def test_nightly_pass_flags_a_mismatched_run(tenant_a: UUID, cfg: SpendConfig) -> None:
    from datetime import UTC, datetime

    _, good, _ = capped_run(tenant_a)
    _, bad, _ = capped_run(tenant_a)
    admin("UPDATE account_runs SET spent_usd = 0.20 WHERE id = %s", bad)
    # a stale open reservation on the good run: counted, recorded as unreconciled, not a failure
    admin(
        "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event, owner, lease_epoch,"
        " provider_code, operation, reserved_usd, created_at) VALUES (%s, %s, 'h', 1, 'started', 'w', 1,"
        " 'p_search', 'web_search', 0.005, now() - interval '1 hour')",
        tenant_a, good,
    )
    admin("UPDATE account_runs SET spent_usd = 0.005 WHERE id = %s", good)
    result = await spend_invariants(SessionLocal, tenant_txn, datetime.now(UTC), batch_size=10_000)
    assert bad in result.failed and good not in result.failed and good in result.stale_runs
    assert admin("SELECT unreconciled_usd FROM account_runs WHERE id = %s", good)[0][0] > 0
    capped = await spend_invariants(SessionLocal, tenant_txn, datetime.now(UTC), batch_size=1)
    assert capped.exit_reason == "cap_reached" and capped.checked == 1


@pytest.mark.asyncio
async def test_prepulled_files_register_once(tenant_a: UUID, cfg: SpendConfig) -> None:
    _, run, _ = capped_run(tenant_a)
    a = await service.register_prepulled(tenant_txn, tenant_a, run, "p_signals", "job_openings", b"synthetic", 3, "operator-1", "ref/a")
    b = await service.register_prepulled(tenant_txn, tenant_a, run, "p_signals", "job_openings", b"other", 9, "operator-2", "ref/b")
    assert len(a) == 64 and a != b
    assert admin("SELECT sha256, record_count FROM prepulled_files WHERE run_id = %s", run) == [(a, 3)]


def test_insert_only_tables_refuse_update_and_delete(tenant_a: UUID) -> None:
    for table in ("run_cap_versions", "run_cap_events", "balance_snapshots", "spend_reports", "prepulled_files"):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(tenant_a, f"DELETE FROM {table}")
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(tenant_a, f"UPDATE {table} SET run_id = run_id")


@pytest.mark.asyncio
async def test_tenant_role_cannot_read_the_operator_report(tenant_a: UUID, cfg: SpendConfig) -> None:
    run, p, _ = await _proposed(tenant_a, cfg)
    await service.close_run(tenant_txn, tenant_a, run, cfg, NOW, 60)
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        c.execute("SET LOCAL ROLE public_reader")
        assert c.execute("SELECT count(*) FROM spend_reports WHERE run_id = %s", (run,)).fetchone()[0] == 1
        assert c.execute("SELECT tenant_json FROM spend_reports WHERE run_id = %s", (run,)).fetchone()[0]["run_id"] == str(run)
    with pytest.raises(errors.InsufficientPrivilege), psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        c.execute("SET LOCAL ROLE public_reader")
        c.execute("SELECT operator_json FROM spend_reports")
