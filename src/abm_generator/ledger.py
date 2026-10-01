"""Tool-call ledger: exactly-once *billing* for paid calls (I4).

Every paid call (broker or LLM) goes through :func:`ledgered_call`. The ledger key is a
hash of the tool name and canonicalised arguments, unique per run
(``UNIQUE (run_id, args_hash)``). A replay after a crash finds the stored response and
returns it without calling the vendor again.

Guarantee scope: exactly-once *billing*, not exactly-once side effects. A crash between
the vendor call and the ledger insert can repeat one call; keep paid calls idempotent
where the vendor supports it (Spec 5.5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from abm_generator.db.session import tenant_txn


def args_hash(tool: str, args: dict[str, Any]) -> str:
    """Canonical SHA-256 hash of tool name and arguments (sorted keys, fixed separators)."""
    payload = json.dumps([tool, args], sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _lookup(session: AsyncSession, run_id: UUID, h: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text("SELECT response_json FROM tool_calls WHERE run_id = :r AND args_hash = :h"),
            {"r": run_id, "h": h},
        )
    ).first()
    if row is None:
        return None
    parsed: dict[str, Any] = json.loads(row.response_json)
    return parsed


async def ledgered_call(  # noqa: PLR0917 - (tenant, run, tool, args, fn) is the ledger contract
    tenant_id: UUID,
    run_id: UUID,
    tool: str,
    args: dict[str, Any],
    fn: Callable[..., Awaitable[dict[str, Any]]],
    cost_usd: Decimal | None = None,
) -> dict[str, Any]:
    """Call ``fn`` once per (run_id, tool, args); replay from the ledger afterwards.

    Cost resolution order: explicit ``cost_usd``, else ``result["cost_usd"]`` else 0.
    Insert uses ON CONFLICT DO NOTHING so concurrent duplicates cannot double-charge.
    """
    h = args_hash(tool, args)

    async with tenant_txn(tenant_id) as session:
        replay = await _lookup(session, run_id, h)
        if replay is not None:
            replay.setdefault("_replayed", True)
            return replay

    result = await fn(**args)

    resolved_cost = cost_usd if cost_usd is not None else Decimal(str(result.get("cost_usd", 0)))
    async with tenant_txn(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash,"
                " response_json, cost_usd)"
                " VALUES ((SELECT app_tenant()), :r, :t, :h, CAST(:resp AS jsonb), :c)"
                " ON CONFLICT (run_id, args_hash) DO NOTHING"
            ),
            {
                "r": run_id,
                "t": tool,
                "h": h,
                "resp": json.dumps(result, default=repr),
                "c": resolved_cost,
            },
        )
    return result
