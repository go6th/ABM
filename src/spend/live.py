"""Live spend (M2): one consistent read of the counters, two allow-listed views, a labelled
projection.

``snapshot`` reads everything in ONE ``REPEATABLE READ, READ ONLY`` transaction, so ``committed``,
``held`` (open reservations), ``cap``, ``soft``, ``remaining`` and the per-provider breakdown all
describe the same instant, stamped ``as_of``. Only ``committed`` is claimed monotonic (held moves
both ways as calls start and settle).

* **Operator view**: amounts in micro-dollars by provider code and category, the funding mode,
  and the **projection** (committed plus held plus the estimate of the funded pending units),
  always labelled ``estimate`` with its base and the number of pending units whose cost is
  unknown (never counted as zero).
* **Tenant view**: an allow-listed status (``within_limit``, ``near_limit``,
  ``waiting_for_approval``, ``stopped_at_limit``), call counts by category and ``as_of``: no
  provider name or code, no amount, no credit count (D-04, D-30).

GUARANTEE(test_live_snapshot_is_consistent_under_concurrent_reserves): M2 on Postgres.
GUARANTEE(test_tenant_live_view_is_allow_listed): no amount, no provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.config import SpendConfig, V2Config, load_spend_config, load_v2_config
from spend.limits import Mode, mode, read_inputs, soft_threshold
from spend.money import usd_to_micros

SnapshotTxn = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
TENANT_STATUS = {
    Mode.NORMAL: "within_limit",
    Mode.PRIORITY_ONLY: "near_limit",
    Mode.PAUSED: "waiting_for_approval",
    Mode.STOPPED: "stopped_at_limit",
}
TENANT_KEYS = frozenset({"run_id", "status", "calls_by_category", "as_of"})


@dataclass(frozen=True)
class PendingEstimate:
    """Upper-bound estimates of the funded pending units (from the unit plan)."""

    micros: int
    units: int
    unknown_units: int


@dataclass(frozen=True)
class Snapshot:
    """The operator view (amounts in micro-dollars)."""

    run_id: str
    as_of: str
    cap_version: int | None
    cap_micros: int | None
    soft_micros: int | None
    committed_micros: int
    held_micros: int
    remaining_micros: int | None
    mode: Mode
    by_provider: tuple[dict[str, Any], ...]
    projection: dict[str, Any]

    def tenant_view(self, calls_by_category: dict[str, int]) -> dict[str, Any]:
        """The allow-listed tenant view (see the module docstring)."""
        view = {
            "run_id": self.run_id,
            "status": TENANT_STATUS[self.mode],
            "calls_by_category": dict(sorted(calls_by_category.items())),
            "as_of": self.as_of,
        }
        assert set(view) == TENANT_KEYS
        return view


async def snapshot(
    txn: Callable[..., AbstractAsyncContextManager[AsyncSession]],
    tenant_id: UUID,
    run_id: UUID,
    now: datetime,
    pending: PendingEstimate | None = None,
    cfg: V2Config | None = None,
    spend_cfg: SpendConfig | None = None,
) -> tuple[Snapshot, dict[str, int]] | None:
    """The snapshot and the tenant's calls by category, from one consistent read."""
    cfg = cfg or load_v2_config()
    spend_cfg = spend_cfg or load_spend_config()
    async with txn(tenant_id, snapshot=True) as s:
        inputs = await read_inputs(s, run_id)
        if inputs is None:
            return None
        rows: Sequence[Any] = (
            await s.execute(
                text(
                    "SELECT provider_code, operation, done_calls, charged_usd"
                    " FROM run_spend_by_provider WHERE run_id = :r"
                    " ORDER BY provider_code, operation"
                ),
                {"r": run_id},
            )
        ).all()
    by_provider: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    for row in rows:
        category = spend_cfg.category(str(row.operation))
        by_category[category] = by_category.get(category, 0) + int(row.done_calls)
        by_provider.append(
            {
                "provider_code": row.provider_code,
                "category": category,
                "done_calls": int(row.done_calls),
                "charged_micros": usd_to_micros(row.charged_usd),
            }
        )
    cap = inputs.cap_micros
    soft = soft_threshold(cap, cfg.soft_limit_pct) if cap is not None else None
    used = inputs.committed_micros + inputs.held_micros
    pend = pending or PendingEstimate(0, 0, 0)
    projection = {
        "label": "estimate",
        "micros": used + pend.micros,
        "base": "committed plus held plus the upper-bound estimates of funded pending units",
        "pending_units": pend.units,
        "pending_units_with_unknown_cost": pend.unknown_units,
    }
    snap = Snapshot(
        run_id=str(run_id),
        as_of=now.isoformat(),
        cap_version=inputs.cap_version,
        cap_micros=cap,
        soft_micros=soft,
        committed_micros=inputs.committed_micros,
        held_micros=inputs.held_micros,
        remaining_micros=None if cap is None else cap - used,
        mode=mode(inputs, cfg.soft_limit_pct),
        by_provider=tuple(by_provider),
        projection=projection,
    )
    return snap, by_category
