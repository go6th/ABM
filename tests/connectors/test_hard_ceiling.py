"""SD-02: a hard dollar ceiling per research run that no switch lifts.

``RESEARCH_CAPS=off`` gives a run a $1,000,000 budget and lifts the platform dollar cap, and
``TOKEN_GUARD=off`` lifts the token pause, so before this a run had no spend limit at all. The
ceiling (``spend_guard.HARD_RUN_CEILING_USD``, $75, owner decision 2026-11-10) is checked in the
one door every paid call passes, ``spend_guard.check_cap`` (provider calls in the ledger claim,
model calls in ``RunDeps``), against the run's real spend (provider ledger plus priced model
replies). Reaching it refuses the call before the provider is paid and ends the run PARTIAL through
the existing stop path, with ``HardCeilingReached`` as the recorded reason.

GUARANTEE(test_with_both_switches_off_the_hard_ceiling_still_stops_the_run)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import ConnectorError
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.spend_guard import (
    HARD_RUN_CEILING_USD,
    DollarCapReached,
    HardCeilingReached,
)
from tests.connectors.conftest import Harness, Reply, admin_dsn, context, make_run

pytestmark = [pytest.mark.requires_postgres]


def _sql(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _model_cost(tenant: UUID, run: UUID, key: str, usd: str) -> None:
    _sql(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, cost_usd, cost_known) VALUES (%s, %s, %s, 'extract', 'extractor',"
        " 'fake-model', 'model', %s, true)",
        tenant,
        run,
        key,
        usd,
    )


def test_the_ceiling_is_the_owner_value_and_not_a_connector_error() -> None:
    assert Decimal("75.00") == HARD_RUN_CEILING_USD
    # it ends the run like the dollar cap does, never as one failed row
    assert issubclass(HardCeilingReached, DollarCapReached)
    assert not issubclass(HardCeilingReached, ConnectorError)


@pytest.mark.asyncio
async def test_with_both_switches_off_the_hard_ceiling_still_stops_the_run(
    harness: Harness, tenant_a: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.platform.hermetic import MemoryPayloadStore, ScriptedLlm
    from account_intelligence.workflow.deps import RunDeps
    from account_intelligence.workflow.runner import _stop_unexpected
    from account_intelligence.workflow.spec import load_spec
    from tests.connectors.conftest import FakeClock

    monkeypatch.setenv("RESEARCH_CAPS", "off")
    monkeypatch.setenv("TOKEN_GUARD", "off")
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    _sql("UPDATE account_runs SET budget_usd = 1000000, tool_call_cap = 1000000 WHERE id = %s", run)
    search = harness.providers[ProviderCode.P_SEARCH]
    search.script(Reply.cassette("p_search", "web_search"))
    search.script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime()
    # well under the ceiling: with the caps off the call is paid as before
    _model_cost(tenant_a, run, "k1", "70.00")
    await call_operation(
        Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding round",)), ctx, rt
    )
    assert search.calls == 1
    # the run's model replies take it past the ceiling: the next paid call is refused unpaid
    _model_cost(tenant_a, run, "k2", "5.50")
    with pytest.raises(HardCeilingReached) as stopped:
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=("acme chief executive",)), ctx, rt
        )
    assert search.calls == 1
    assert stopped.value.cap == HARD_RUN_CEILING_USD and stopped.value.spent >= Decimal("75")
    # and so is a paid model call
    deps = RunDeps(
        tenant_a, run, account, domain, tenant_txn, ScriptedLlm(lambda *a: "{}"),
        MemoryPayloadStore(), FakeClock(), load_spec(),
    )  # fmt: skip
    with pytest.raises(HardCeilingReached):
        await deps.model("abm_pass2_extract", "sys", "past the ceiling")
    # the existing stop path ends the run PARTIAL with the reason and keeps what it holds
    await _stop_unexpected(deps, stopped.value)
    assert _sql("SELECT status, finished_reason FROM account_runs WHERE id = %s", run) == [
        ("PARTIAL", "HardCeilingReached")
    ]
    assert _sql("SELECT count(*) FROM tool_calls WHERE run_id = %s", run) == [(1,)]
