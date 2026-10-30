"""Offline end to end (FIX_LOG Phase 3.7): with the hermetic model (``LLM_FAKE=hermetic``) and the
hermetic providers (``CONNECTORS=hermetic``: synthetic replies, no outside call), a company run
started as a credit-confirmed research job and a file analysis with public sources both run to
the end through the real application, worker endpoint, database, export builder and pre-delivery
gate: the gate passes and the five client files are built. SYNTHETIC data only.

GUARANTEE(test_a_company_job_and_a_file_run_finish_offline_with_five_files_and_a_passed_gate)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.app.container import delivery_gate, hermetic_connectors
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import SystemClock
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.runs import lifecycle
from tests.connectors.conftest import Harness
from tests.documents.test_file_runs import indexed_documents, start_file_run
from tests.system.hermetic_stack import Stack, admin, run_row

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
FIVE = {"summary", "evidence", "people", "jobs", "tech"}


def offline(harness: Harness) -> Stack:
    """The hermetic stack with the platform's own hermetic providers instead of the harness."""
    stack = Stack.build(harness)
    c = stack.plane.app.state.container
    c.clock = SystemClock()
    c.connectors, retry = hermetic_connectors({}, c.payloads)
    c.documents.scheduler = c.scheduler
    c.gate = delivery_gate(c.payloads, c.documents)
    return stack


def files_and_gate(run: object) -> tuple[set[str], list[Any]]:
    kinds = {k for (k,) in admin("SELECT kind FROM deliverables WHERE run_id = %s", run)}
    gate = admin(
        "SELECT verdict, defects FROM run_gate_results WHERE run_id = %s ORDER BY id DESC LIMIT 1",
        run,
    )
    return kinds, gate


@pytest.fixture(autouse=True)
def _unsigned_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker endpoint of the same application accepts the test's task calls."""
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")


async def test_a_company_job_and_a_file_run_finish_offline_with_five_files_and_a_passed_gate(
    harness: Harness,
) -> None:
    stack = offline(harness)
    a = await stack.client("offline")
    container = stack.plane.app.state.container

    # a company: a confirmed research job on the client's pool, run by the production engine
    async with tenant_txn(a.tenant) as s:
        account, _ = await lifecycle.add_account(s, a.tenant, "acme.example", added_by="u1")
        job, status = await CR.reserve(
            s,
            account_id=account,
            kind="new_research",
            scope="full",
            credits=200,
            actor="u1",
            now=datetime.now(UTC),
        )
    assert status == "queued"
    assert await jobs.run_job(a.tenant, job, jobs.GraphEngine(container)) == "started"
    (run,) = admin("SELECT run_id FROM research_jobs WHERE id = %s", job)[0]
    await stack.drain(a, str(run))
    company = run_row(str(run))
    kinds, gate = files_and_gate(run)
    assert company["status"] in ("COMPLETED", "PARTIAL"), (company, gate)
    assert kinds >= FIVE, (kinds, gate)
    assert gate and gate[0][0] == "pass", gate
    assert admin("SELECT status FROM research_jobs WHERE id = %s", job) == [("done",)]
    assert admin("SELECT count(*) FROM internal_deliverables WHERE run_id = %s", run) == [(1,)]

    # a file analysis with public sources: the file run (Internal Only) and its public run
    docs, _ = await indexed_documents(stack, a)
    started = await start_file_run(stack, a, docs, enrich_public=True)
    assert started.status_code == 202, started.text
    file_run = started.json()["run_id"]
    ((child,),) = admin(
        "SELECT id FROM account_runs WHERE run_input->>'linked_file_run' = %s", file_run
    )
    # the public research of a file analysis is sized from the research plan like a job (the
    # default of 60 calls ended it after a few rows with the caps on)
    ((cap,),) = admin("SELECT tool_call_cap FROM account_runs WHERE id = %s", child)
    assert cap > 200, cap
    await stack.drain(a, file_run)
    await stack.drain(a, str(child))
    assert run_row(file_run)["status"] == "COMPLETED"
    assert files_and_gate(file_run)[1][0][0] == "pass"
    public_kinds, public_gate = files_and_gate(child)
    assert public_kinds >= FIVE and public_gate[0][0] == "pass", (public_kinds, public_gate)
    # nothing left the process: every provider call is in the ledger, none failed on a network
    assert admin(
        "SELECT count(*) FROM tool_call_events WHERE run_id = %s AND event = 'failed'",
        UUID(str(run)),
    ) == [(0,)]
    await stack.close(a)
