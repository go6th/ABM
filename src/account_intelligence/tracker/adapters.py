"""Production adapters for the tracker ports, on the caller's tenant transaction.

``LedgerCallAdapter`` resolves a call reference (the connector ledger's ``args_hash``) to what
the ledger recorded: a ``done`` row in ``tool_calls`` (success, record count, or the error class
of a refused paid body) or a ``failed`` event in ``tool_call_events``. RLS limits both to the
tenant; the ``run_id`` filter limits them to this run, so another run's or a previous run's
reference is not found and the service raises ``ProofMismatch`` (T2).

``RunInputAdapter`` reads ``account_runs.run_input`` (lanes switched off, pursuing client) and the
tenant's other account domains for the detail scrub (T9).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.tracker.model import CallRecord


def _queries(args: Mapping[str, Any]) -> tuple[str, ...]:
    """The query text a call ran, from its stored arguments (search-style operations)."""
    found: list[str] = []
    for key in ("queries", "query", "keyword", "keywords", "titles", "technology", "url"):
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list | tuple):
            found.extend(str(v) for v in value)
    return tuple(found)


class LedgerCallAdapter:
    """``CallLedgerPort`` over the connector ledger tables."""

    def __init__(self, session: AsyncSession, category_by_operation: Mapping[str, str]):
        self.session = session
        self.category_by_operation = dict(category_by_operation)

    def _category(self, operation: str | None) -> str:
        if operation is None or operation not in self.category_by_operation:
            raise KeyError(f"operation {operation!r} has no tool category")
        return self.category_by_operation[operation]

    async def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        """The ledger record of one call of this tenant and run (KeyError otherwise)."""
        done = (
            await self.session.execute(
                text(
                    "SELECT operation, response FROM tool_calls"
                    " WHERE run_id = :r AND args_hash = :h AND tenant_id = :t"
                ),
                {"r": run_id, "h": call_ref, "t": tenant_id},
            )
        ).first()
        if done is not None:
            meta = done.response or {}
            error = meta.get("error_class")
            return CallRecord(
                call_ref=call_ref,
                tool_category=self._category(done.operation),
                success=error is None,
                result_count=meta.get("record_count") if error is None else None,
                error_kind=error,
                queries=_queries(meta.get("args") or {}),
                tenant_id=str(tenant_id),
                run_id=str(run_id),
            )
        failed = (
            await self.session.execute(
                text(
                    "SELECT operation, error_class FROM tool_call_events"
                    " WHERE run_id = :r AND args_hash = :h AND tenant_id = :t AND event = 'failed'"
                    " ORDER BY attempt DESC LIMIT 1"
                ),
                {"r": run_id, "h": call_ref, "t": tenant_id},
            )
        ).first()
        if failed is None:
            raise KeyError(call_ref)
        return CallRecord(
            call_ref=call_ref,
            tool_category=self._category(failed.operation),
            success=False,
            result_count=None,
            error_kind=failed.error_class,
            tenant_id=str(tenant_id),
            run_id=str(run_id),
        )


class RunInputAdapter:
    """``RunInputPort`` over ``account_runs.run_input`` and the tenant's accounts."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _input(self, run_id: UUID) -> dict[str, Any]:
        row = (
            await self.session.execute(
                text("SELECT run_input FROM account_runs WHERE id = :r"), {"r": run_id}
            )
        ).first()
        return dict(row.run_input) if row is not None and row.run_input else {}

    async def is_lane_in_scope(self, tenant_id: UUID, run_id: UUID, lane: str) -> bool:
        """A lane is out of scope only when the run input lists it as off (verified, T4b)."""
        return lane not in set((await self._input(run_id)).get("lanes_off") or [])

    async def other_account_names(self, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
        """Domains of the tenant's other accounts (never to appear in this run's output)."""
        result = await self.session.execute(
            text(
                "SELECT a.domain FROM accounts a WHERE a.tenant_id = :t AND a.id <>"
                " (SELECT account_id FROM account_runs WHERE id = :r)"
            ),
            {"t": tenant_id, "r": run_id},
        )
        return tuple(sorted(r.domain for r in result))
