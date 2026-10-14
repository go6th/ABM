"""Durable runs: fault injection, one stop reason per test, on the SYNTHETIC whole-run world.

For every stop reason the assertions are the same four: no finished paid call is lost (every
paid key has its ledger row and the ledger sum equals the run's spend); the status and stop
reason are right; a resume reuses what is stored and pays only for the missing part; another
tenant sees none of the data.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import SessionLocal, tenant_txn
from account_intelligence.runs.sweeper import sweep
from exports.builder import BuildFailed
from exports.models import BuildResult
from llm.errors import ContextTooLong, ProviderCreditsExhausted
from tests.connectors.conftest import Harness, context, make_run
from tests.system.world import (
    LEDGER_SUM,
    Script,
    admin,
    create_only,
    credit_entries,
    drive,
    settled_once,
    start_run,
)
from tests.tracker.world import tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

SECRET = "sk-ant-api03-" + "Z" * 40


def execute(sql: str, *params: object) -> None:
    """An admin statement that returns no rows."""
    import psycopg

    from tests.tracker.world import admin_dsn

    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(sql, params)


class Interrupt(BaseException):
    """A SIGINT-like stop (KeyboardInterrupt itself would stop the test runner's loop)."""


def run_state(run: UUID) -> tuple[str, str | None, str | None]:
    ((status, reason, detail),) = admin(
        "SELECT status, finished_reason, stop_detail FROM account_runs WHERE id = %s", run
    )
    return status, reason, detail


def paid_calls(run: UUID) -> int:
    return int(
        admin(
            "SELECT count(*) FROM tool_calls WHERE run_id = %s AND response_ref IS NOT NULL", run
        )[0][0]
    )


def assert_ledger_consistent(run: UUID) -> None:
    """Every paid key is recorded once and the ledger sum is the run's spend."""
    rows, distinct = admin(
        "SELECT count(*), count(DISTINCT args_hash) FROM tool_calls WHERE run_id = %s", run
    )[0]
    assert rows == distinct
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run)[0][0]
    assert Decimal(admin(LEDGER_SUM, run)[0][0]) == Decimal(spent)
    dup = admin("SELECT count(*) FROM tool_calls WHERE run_id = %s AND possible_duplicate", run)[0][
        0
    ]
    assert dup <= 1  # at most one repeat per crash


async def assert_other_tenant_sees_nothing(run: UUID) -> None:
    other = uuid4()
    admin("INSERT INTO tenants (id, name) VALUES (%s, %s) RETURNING id", other, f"other-{other}")
    async with tenant_txn(other) as s:
        for table in ("account_runs", "tool_calls", "facts", "coverage_rows", "model_reply_log"):
            column = "id" if table == "account_runs" else "run_id"
            count = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {column} = :r"),  # noqa: S608
                    {"r": run},
                )
            ).scalar_one()
            assert count == 0, table


class Failing(Script):
    """The scripted model fails on the given tier with the given error (after ``after`` calls)."""

    def __init__(self, tier: str, error: BaseException, after: int = 0) -> None:
        super().__init__()
        self.tier_to_fail, self.error, self.after = tier, error, after

    def __call__(self, tier: str, system: str, user: str) -> str:
        if tier == self.tier_to_fail:
            self.after -= 1
            if self.after < 0:
                raise self.error
        return super().__call__(tier, system, user)


# ------------------------------------------------------------------ unhandled exception


async def test_an_unexpected_error_ends_partial_when_anything_is_stored(harness: Harness) -> None:
    """A model-side defect carrying a key: the run keeps its pulls, ends PARTIAL with the error
    class as the reason and a redacted detail; FAILED only without any stored data."""
    run = await start_run(harness, Failing("extractor", RuntimeError(f"boom key={SECRET}")))
    with pytest.raises(RuntimeError):
        await run.scheduler.drain()
    status, reason, detail = run_state(run.run)
    expected = "PARTIAL" if paid_calls(run.run) else "FAILED"
    assert (status, reason) == (expected, "RuntimeError")
    assert detail is not None and detail.startswith("RuntimeError: boom")
    assert SECRET not in detail and "[redacted]" in detail
    assert_ledger_consistent(run.run)
    await assert_other_tenant_sees_nothing(run.run)
    if expected == "PARTIAL":
        # a PARTIAL run is charged what it used; only FAILED charges nothing
        assert settled_once(run.run)


async def test_a_run_with_nothing_stored_ends_failed_with_a_full_refund(harness: Harness) -> None:
    run = await start_run(harness, Failing("planner", RuntimeError("defect before any pull")))
    with pytest.raises(RuntimeError):
        await run.scheduler.drain()
    status, reason, _ = run_state(run.run)
    if paid_calls(run.run) == 0:
        assert (status, reason) == ("FAILED", "RuntimeError")
        entries = credit_entries(run.run)
        assert "settle" not in entries and entries["release"] == entries["reserve"]
    else:  # a free setup pull happened first: then PARTIAL is the right answer
        assert (status, reason) == ("PARTIAL", "RuntimeError")


# ------------------------------------------------------------------ model-side stop reasons


@pytest.mark.parametrize(
    "error",
    [ProviderCreditsExhausted("credit balance too low"), ContextTooLong("prompt is too long")],
    ids=["model_credits_exhausted", "prompt_too_long"],
)
async def test_a_terminal_model_error_keeps_the_pulls_and_ends_partial(
    harness: Harness, error: BaseException
) -> None:
    run = await start_run(harness, Failing("extractor", error))
    with pytest.raises(type(error)):
        await run.scheduler.drain()
    status, reason, detail = run_state(run.run)
    assert status == "PARTIAL" and reason == type(error).__name__
    assert detail is not None and detail.startswith(type(error).__name__)
    assert paid_calls(run.run) > 0
    assert_ledger_consistent(run.run)
    await assert_other_tenant_sees_nothing(run.run)


async def test_an_invalid_model_reply_is_stored_and_the_run_goes_on(harness: Harness) -> None:
    class Garbage(Script):
        def __call__(self, tier: str, system: str, user: str) -> str:
            if tier == "extractor":
                self.calls[tier] = self.calls.get(tier, 0) + 1
                return "Sure! Here is the ```json {truncated"
            return super().__call__(tier, system, user)

    run = await start_run(harness, Garbage())
    await run.scheduler.drain()
    status, _, _ = run_state(run.run)
    assert status == "PARTIAL"  # the run never dies on an invalid reply
    stored = [k for k in harness.payloads.objects if "/model_replies/" in k]
    assert stored, "every reply is stored before it is parsed"
    assert_ledger_consistent(run.run)


# ------------------------------------------------------------------ provider-side stop reasons


async def test_a_network_drop_mid_provider_call_blocks_the_row_not_the_run(
    harness: Harness,
) -> None:
    from tests.connectors.conftest import Reply

    run = await start_run(harness)
    harness.providers[ProviderCode.P_SIGNALS].fallback = Reply(status=503, body={})
    await run.scheduler.drain()
    status, _, _ = run_state(run.run)
    assert status == "PARTIAL"
    reasons = dict(
        admin("SELECT row_key, reason_code FROM coverage_rows WHERE run_id = %s", run.run)
    )
    assert reasons["job_postings_12_months"] == "source_unavailable"
    assert_ledger_consistent(run.run)


async def test_a_paid_body_whose_parsing_fails_is_linked_not_lost(
    harness: Harness, tenant_a: UUID
) -> None:
    """Write-ahead after the response: a defect in parsing leaves a done row (error class, cost,
    payload reference); the resume replays the stored body and never pays again."""
    from tests.connectors.conftest import Reply

    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    rt = harness.runtime()
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    inner = rt.connectors[ProviderCode.P_SEARCH]

    class Broken:
        def __getattr__(self, name: str) -> Any:
            return getattr(inner, name)

        def parse(self, *args: Any, **kwargs: Any) -> Any:
            raise ValueError("parser defect")

    connectors = dict(rt.connectors)
    connectors[ProviderCode.P_SEARCH] = Broken()  # type: ignore[assignment]
    from dataclasses import replace

    broken_rt = replace(rt, connectors=connectors)
    search = WebSearchArgs(queries=("acme funding round",))
    with pytest.raises(ValueError):
        await call_operation(Operation.WEB_SEARCH, search, ctx, broken_rt)
    rows = admin(
        "SELECT response_ref, response->>'error_class', cost_usd FROM tool_calls WHERE run_id = %s",
        run,
    )
    assert len(rows) == 1 and rows[0][0] is not None and rows[0][1] == "ValueError"
    assert Decimal(rows[0][2]) > 0  # the paid body is charged once
    with pytest.raises(ValueError):  # the replay is deterministic and free
        await call_operation(Operation.WEB_SEARCH, search, ctx, broken_rt)
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1
    assert_ledger_consistent(run)


async def test_provider_credits_exhausted_blocks_the_row_and_keeps_the_rest(
    harness: Harness,
) -> None:
    from tests.connectors.conftest import Reply

    run = await start_run(harness)
    # the structured-signals provider says the balance is empty (HTTP 402 in its error table)
    harness.providers[ProviderCode.P_SIGNALS].fallback = Reply(status=402, body={"error": "x"})
    await run.scheduler.drain()
    status, _, _ = run_state(run.run)
    assert status in ("PARTIAL", "BUDGET_EXCEEDED")
    blocked = admin(
        "SELECT count(*) FROM coverage_rows WHERE run_id = %s AND status = 'blocked'", run.run
    )[0][0]
    assert blocked >= 1
    assert paid_calls(run.run) > 0
    assert_ledger_consistent(run.run)


# ------------------------------------------------------------------ interrupts and crashes


async def _interrupt_after_responses(harness: Harness, kind: BaseException, at: int = 3):  # type: ignore[no-untyped-def]
    """Start a run in one 'process' and stop it ``at`` responses in, as the kind of stop given."""
    tenant, run = await create_only(harness)
    seen = 0

    async def after_response() -> None:
        nonlocal seen
        seen += 1
        if seen >= at:
            if isinstance(kind, asyncio.CancelledError):
                asyncio.current_task().cancel()  # type: ignore[union-attr]
                await asyncio.sleep(0)
            raise kind

    with pytest.raises(BaseException):  # noqa: B017, PT011 - the exact wrapper is LangGraph's
        await drive(harness, Script(), tenant, run, after_response_hook=after_response)
    return tenant, run


@pytest.mark.parametrize(
    "kind", [Interrupt(), asyncio.CancelledError()], ids=["sigint", "sigterm_cancel"]
)
async def test_an_interrupted_slice_leaves_the_run_resumable_and_resumes(
    harness: Harness, kind: BaseException
) -> None:
    tenant, run = await _interrupt_after_responses(harness, kind)
    status, _, detail = run_state(run)
    assert status == "RUNNING"  # not FAILED: the run resumes from its checkpoint
    assert detail is not None and detail.startswith("interrupted: ")
    assert type(kind).__name__ in detail  # the interrupt is named, wrapped or not
    assert (
        admin("SELECT count(*) FROM run_leases WHERE run_id = %s AND expires_at > now()", run)[0][0]
        == 0
    )  # the lease was released on the way out
    before = paid_calls(run)
    calls_before = sum(p.calls for p in harness.providers.values())
    await drive(harness, Script(), tenant, run)
    status, reason, _ = run_state(run)
    assert (status, reason) == ("PARTIAL", "builders_not_configured")
    assert paid_calls(run) >= before  # nothing finished was lost
    # the one response the interrupt cut off before its ledger write is re-issued at most once
    assert sum(p.calls for p in harness.providers.values()) >= calls_before
    assert_ledger_consistent(run)
    await assert_other_tenant_sees_nothing(run)


async def test_the_sweeper_ends_an_orphaned_run_partial_and_keeps_its_data(
    harness: Harness,
) -> None:
    """A worker killed (OOM, deploy) never writes again: its run stays RUNNING with an expired
    lease until the sweeper ends it worker_lost and keeps every stored pull."""
    tenant, run = await _interrupt_after_responses(harness, Interrupt())
    before = paid_calls(run)
    # a second run of another tenant with a LIVE lease is not touched
    other_tenant, other_run = await create_only(harness)
    execute("UPDATE account_runs SET status = 'RUNNING' WHERE id = %s", other_run)
    admin(
        "INSERT INTO run_leases (run_id, tenant_id, owner, epoch, expires_at, heartbeat_at)"
        " VALUES (%s, %s, 'alive', 1, now() + interval '5 minutes', now()) RETURNING run_id",
        other_run,
        other_tenant,
    )
    # the dead worker's lease expired an hour ago
    execute(
        "UPDATE run_leases SET expires_at = now() - interval '1 hour',"
        " heartbeat_at = now() - interval '1 hour' WHERE run_id = %s",
        run,
    )
    result = await sweep(SessionLocal, tenant_txn, tracker, datetime.now(UTC), orphan_after_s=600)
    assert run in result.partial and other_run not in result.partial + result.failed
    status, reason, detail = run_state(run)
    assert (status, reason) == ("PARTIAL", "worker_lost")
    assert detail is not None and detail.startswith("worker_lost")
    assert paid_calls(run) == before  # data kept
    assert run_state(other_run)[0] == "RUNNING"
    assert_ledger_consistent(run)
    await assert_other_tenant_sees_nothing(run)
    # a second pass changes nothing (the run is terminal)
    again = await sweep(SessionLocal, tenant_txn, tracker, datetime.now(UTC), orphan_after_s=600)
    assert run not in again.partial + again.failed


async def test_the_sweeper_leaves_live_and_waiting_runs_alone(harness: Harness) -> None:
    waiting_tenant, waiting = await create_only(harness, auto_approve=False)
    execute(
        "UPDATE account_runs SET status = 'AWAITING_APPROVAL', created_at = now() - interval"
        " '2 hours' WHERE id = %s",
        waiting,
    )
    result = await sweep(SessionLocal, tenant_txn, tracker, datetime.now(UTC), orphan_after_s=600)
    assert waiting not in result.partial + result.failed
    assert run_state(waiting)[0] == "AWAITING_APPROVAL"


# ------------------------------------------------------------------ build and resume


async def test_a_failed_build_ends_partial_never_failed(harness: Harness) -> None:
    class RefusingBuilder:
        async def build(self, tenant_id: str, run_id: str) -> list[str]:
            raise BuildFailed(
                BuildResult(status="failed", reason="build_failed", errors=("missing_narrative",))
            )

    tenant, run = await create_only(harness)
    await drive(harness, Script(), tenant, run, builder=RefusingBuilder())
    status, reason, _ = run_state(run)
    assert (status, reason) == ("PARTIAL", "build_failed")
    assert paid_calls(run) > 0
    assert_ledger_consistent(run)


async def test_resume_from_reuses_every_finished_call_at_cost_zero(harness: Harness) -> None:
    """A run stopped by a model defect is continued by a new run naming it: every finished pull
    is adopted at cost 0 (rows marked cache), the provider is not called for them, and only the
    work the first run never reached is paid for."""
    first = await start_run(harness, Failing("extractor", RuntimeError("stopped")))
    with pytest.raises(RuntimeError):
        await first.scheduler.drain()
    assert run_state(first.run)[0] == "PARTIAL"
    stored = paid_calls(first.run)
    assert stored > 0
    provider_calls = sum(p.calls for p in harness.providers.values())
    # age the first run's rows past every TTL: a resume ignores age
    execute(
        "UPDATE tool_calls SET created_at = now() - interval '400 days' WHERE run_id = %s",
        first.run,
    )
    second = await start_run(
        harness, run_input={"resume_from": str(first.run)}, tenant_id=first.tenant
    )
    await second.scheduler.drain()
    status, reason, _ = run_state(second.run)
    assert (status, reason) == ("PARTIAL", "builders_not_configured")
    adopted = admin(
        "SELECT count(*) FROM tool_calls WHERE run_id = %s AND cost_source = 'cache'", second.run
    )[0][0]
    assert adopted == stored  # every finished call of the stopped run came back free
    adopted_cost = admin(
        "SELECT coalesce(sum(cost_usd), 0) FROM tool_calls WHERE run_id = %s"
        " AND cost_source = 'cache'",
        second.run,
    )[0][0]
    assert Decimal(adopted_cost) == 0
    new_calls = sum(p.calls for p in harness.providers.values()) - provider_calls
    paid_new = admin(
        "SELECT count(*) FROM tool_calls WHERE run_id = %s AND cost_source <> 'cache'", second.run
    )[0][0]
    assert new_calls == paid_new  # the provider was called only for the missing part
    assert_ledger_consistent(second.run)
    await assert_other_tenant_sees_nothing(second.run)


async def test_a_started_row_is_never_treated_as_finished(harness: Harness, tenant_a: UUID) -> None:
    """A resume sees the stopped run's ``started`` attempts (no payload) as nothing to reuse."""
    from account_intelligence.connectors.ledger import CallKey, Claimed

    account, run1, domain = make_run(tenant_a)
    rt = harness.runtime()
    key = CallKey(
        args_hash="r" * 64,
        tool="p_search:web_search",
        provider_code="p_search",
        operation="web_search",
    )
    assert isinstance(
        await rt.ledger.claim(context(tenant_a, account, run1, domain), rt.worker_id, key, 0.005),
        Claimed,
    )
    _, run2, _ = make_run(tenant_a, f"other-{uuid4().hex[:6]}.example.com")
    ctx2 = context(tenant_a, account, run2, domain, resume_from=run1)
    claim = await rt.ledger.claim(ctx2, rt.worker_id, key, 0.005)
    assert isinstance(claim, Claimed)  # a new paid attempt, not a replay of a started row
