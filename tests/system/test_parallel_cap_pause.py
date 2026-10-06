"""Pause instead of fail on the whole run path (D-45, M3, M5): a parallel run whose cap is too small
pauses in AWAITING_APPROVAL with ``awaiting_kind = 'cap'`` and exactly one pause request; an
approval appends a cap version and the run resumes to its normal end; a decline or a timeout
resumes it to BUDGET_EXCEEDED with partial results and one settlement.

GUARANTEE(test_cap_pause_interrupts_and_resumes_after_approval)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.workflow import parallel_phase
from account_intelligence.workflow.spec import load_spec
from account_intelligence.workflow.transitions import RunStatus
from spend import pause
from spend.config import load_spend_config, load_v2_config
from tests.connectors.conftest import Harness
from tests.spend.conftest import admin as admin_exec
from tests.system.world import admin, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def parallel_spec() -> Any:
    spec = load_spec()
    return replace(spec, settings={**spec.settings, "execution_mode": "parallel"})


@pytest.fixture
def tight_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the research phase starts, shrink the approved cap to what is spent plus one cent,
    so the first paid wave is denied by the cap (test instrumentation only)."""
    original = parallel_phase.research_node

    def wrapped(handlers: Any, deps: Any, cfg: Any = None) -> Any:
        inner = original(handlers, deps, cfg)

        async def research(state: Any) -> Any:
            row = admin(
                "SELECT spent_usd, cap_version FROM account_runs WHERE id = %s", deps.run_id
            )[0]
            if row[1] == 1:  # only under the first approved cap
                admin_exec(
                    "UPDATE account_runs SET budget_usd = %s WHERE id = %s",
                    Decimal(row[0]) + Decimal("0.01"),
                    deps.run_id,
                )
            return await inner(state)

        return research

    monkeypatch.setattr(parallel_phase, "research_node", wrapped)


def status(run: UUID) -> tuple[Any, ...]:
    return admin(
        "SELECT status, awaiting_kind, finished_reason FROM account_runs WHERE id = %s", run
    )[0]


def approver(tenant: UUID, now: Any) -> pause.Approver:
    return pause.Approver("u1", str(tenant), "admin", step_up_at=now)


async def open_request(run: UUID) -> UUID:
    rows = admin(
        "SELECT request_id FROM spend_limit_events WHERE run_id = %s AND event = 'pause_opened'",
        run,
    )
    assert len(rows) == 1  # exactly one request for the cap version
    return UUID(str(rows[0][0]))


async def resume(tenant: UUID, run: UUID, scheduler: Any) -> None:
    async with tenant_txn(tenant) as s:
        await lifecycle.move(s, run, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
    used = admin("SELECT slices_used FROM account_runs WHERE id = %s", run)[0][0]
    await scheduler.enqueue_slice(tenant, run, int(used))
    await scheduler.drain()


async def test_cap_pause_interrupts_and_resumes_after_approval(
    harness: Harness, tight_cap: None
) -> None:
    run = await start_run(harness, spec=parallel_spec())
    await run.scheduler.drain()
    assert status(run.run)[:2] == ("AWAITING_APPROVAL", "cap")
    request_id = await open_request(run.run)
    denied = admin(
        "SELECT count(*) FROM work_unit_events WHERE run_id = %s AND event = 'cap_denied'", run.run
    )[0][0]
    assert denied >= 1
    outbox = {
        t for (t,) in admin("SELECT template FROM notification_outbox WHERE run_id = %s", run.run)
    }
    assert {"spend_pause_opened", "run_waiting_for_approval"} <= outbox
    now = harness.clock.now()
    async with tenant_txn(run.tenant) as s:
        new_cap = await pause.approve(
            s,
            run.run,
            request_id,
            approver(run.tenant, now),
            None,
            now,
            load_v2_config(),
            load_spend_config(),
            pause.UnconfiguredWallet(),
        )
    await resume(run.tenant, run.run, run.scheduler)
    final, kind, reason = status(run.run)
    assert final in ("PARTIAL", "COMPLETED") and kind is None
    assert reason != "budget_cap"
    budget, version = admin(
        "SELECT budget_usd, cap_version FROM account_runs WHERE id = %s", run.run
    )[0]
    assert version == 2 and Decimal(budget) == Decimal(new_cap) / 1_000_000
    entries = dict(admin("SELECT entry, amount FROM wallet_ledger WHERE run_id = %s", run.run))
    assert -entries["reserve"] == entries["commit"] + entries["refund"]  # one settlement


async def test_a_declined_pause_ends_budget_exceeded_with_partial_results(
    harness: Harness, tight_cap: None
) -> None:
    run = await start_run(harness, spec=parallel_spec())
    await run.scheduler.drain()
    request_id = await open_request(run.run)
    now = harness.clock.now()
    async with tenant_txn(run.tenant) as s:
        await pause.decline(
            s, run.run, request_id, approver(run.tenant, now), now, load_v2_config()
        )
    await resume(run.tenant, run.run, run.scheduler)
    assert status(run.run)[0] == "BUDGET_EXCEEDED"
    rows = admin("SELECT status FROM coverage_rows WHERE run_id = %s", run.run)
    assert len(rows) == 33 and all(r[0] is not None for r in rows)  # every row reported
    entries = admin(
        "SELECT entry FROM wallet_ledger WHERE run_id = %s AND entry = 'commit'", run.run
    )
    assert len(entries) == 1


async def test_a_timed_out_pause_resumes_to_budget_exceeded_never_reopens(
    harness: Harness, tight_cap: None
) -> None:
    run = await start_run(harness, spec=parallel_spec())
    await run.scheduler.drain()
    await open_request(run.run)
    deadline = admin("SELECT approval_deadline FROM account_runs WHERE id = %s", run.run)[0][0]
    async with tenant_txn(run.tenant) as s:
        early = await lifecycle.expire_cap_pause(s, run.run, deadline - timedelta(minutes=1))
        assert early is False
        assert await lifecycle.expire_cap_pause(s, run.run, deadline + timedelta(seconds=1))
    used = admin("SELECT slices_used FROM account_runs WHERE id = %s", run.run)[0][0]
    await run.scheduler.enqueue_slice(run.tenant, run.run, int(used))
    await run.scheduler.drain()
    assert status(run.run)[0] == "BUDGET_EXCEEDED"
    assert admin(
        "SELECT event FROM spend_limit_events WHERE run_id = %s AND event = 'timed_out'", run.run
    ) == [("timed_out",)]
