"""A worker killed on purpose (spec 9.5), run as a subprocess by ``test_kill_resume.py``.

``claim <tenant> <run>``: claim and reserve one paid call through the connector ledger, then die
(``os._exit``) before the call is finalised: a started intent with its reservation and no end.
``finish <tenant> <run>``: start ``lifecycle.finish`` and die while the spend report is being
written: the finishing transaction never commits (no terminal state, no settlement, no report).
Synthetic data only; the database comes from the inherited test environment.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID


async def claim(tenant: UUID, run: UUID) -> None:
    from account_intelligence.connectors.base import (
        CallContext,
        Operation,
        ProviderCode,
        SystemClock,
    )
    from account_intelligence.connectors.budget import BudgetDefaults
    from account_intelligence.connectors.ledger import CallKey, Ledger
    from account_intelligence.db.session import tenant_txn

    ledger = Ledger(
        tenant_txn, SystemClock(), lease_ttl_s=1, budget_defaults=BudgetDefaults(5.0, 60)
    )
    ctx = CallContext(
        tenant_id=tenant, run_id=run, account_id=run, account_domain="synthetic.example"
    )
    key = CallKey(
        args_hash="k" * 64,
        tool="p_search:web_search",
        provider_code=ProviderCode.P_SEARCH,
        operation=Operation.WEB_SEARCH,
        route=None,
        fell_back_from=None,
    )
    await ledger.claim(ctx, "doomed-worker", key, 0.005)
    os._exit(9)  # killed between the reservation and the finalisation


async def finish(tenant: UUID, run: UUID) -> None:
    import spend.service
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.runs import lifecycle
    from account_intelligence.workflow.transitions import RunStatus
    from tests.system.world import tracker

    real = spend.service.close_in_session

    async def dying(s, run_id, *a, **k):  # type: ignore[no-untyped-def]
        await s.execute(__import__("sqlalchemy").text("SELECT 1"))
        os._exit(9)  # killed while the report is being written
        return await real(s, run_id, *a, **k)  # pragma: no cover

    spend.service.close_in_session = dying
    async with tenant_txn(tenant) as s:
        await lifecycle.finish(s, tracker(s), run, RunStatus.CANCELED, reason="operator_cancel")


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    mode, tenant, run = sys.argv[1], UUID(sys.argv[2]), UUID(sys.argv[3])
    asyncio.run({"claim": claim, "finish": finish}[mode](tenant, run))
