"""Run-scoped stores and tenant settings for the agent layer (Postgres under RLS).

* ``PgRunRecords`` is the ``RunRecordStore``: sense checks (``abm_sense_checks``, the latest
  binds the build, G6) and distill outputs (``abm_distill_outputs``, insert-only per audience).
* ``SettingsAdapter`` is the ``SettingsPort``: lanes from the tenant's saved settings (only skill
  lanes), the pursuing client frozen in the run's input at start (S16; else the tenant setting),
  and internal history switched off until its connector search has a binding (D-98, OI-ABM-4).

GUARANTEE(test_distill_outputs_are_stored_once_per_audience)
GUARANTEE(test_settings_adapter_reads_lanes_and_the_frozen_pursuing_client)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text

from account_intelligence.grading.repo import TxnFactory
from agents.adapters import PgSenseChecks
from agents.config import SkillConfig
from agents.ports import InternalHistorySettings, RunContext, TenantAgentSettings
from tenancy import settings as tenant_settings


class PgRunRecords(PgSenseChecks):
    """``RunRecordStore``: sense checks from ``PgSenseChecks`` plus durable distill outputs."""

    def __init__(self, txn: TxnFactory) -> None:
        self.txn = txn

    async def save_distill(self, ctx: RunContext, audience: str, output: BaseModel) -> None:
        """Insert once per run and audience (a replay is a no-op)."""
        async with self.txn(ctx.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO abm_distill_outputs (tenant_id, run_id, audience, output)"
                    " VALUES (:t, :r, :a, CAST(:o AS jsonb))"
                    " ON CONFLICT (tenant_id, run_id, audience) DO NOTHING"
                ),
                {
                    "t": ctx.tenant_id,
                    "r": ctx.run_id,
                    "a": audience,
                    "o": json.dumps(output.model_dump(mode="json"), sort_keys=True),
                },
            )

    async def distill(self, ctx: RunContext, audience: str) -> dict[str, Any] | None:
        """The stored output for an audience."""
        async with self.txn(ctx.tenant_id) as s:
            value = (
                await s.execute(
                    text(
                        "SELECT output FROM abm_distill_outputs WHERE run_id = :r AND audience = :a"
                    ),
                    {"r": ctx.run_id, "a": audience},
                )
            ).scalar_one_or_none()
        return dict(value) if value is not None else None


@dataclass(frozen=True)
class SettingsAdapter:
    """See the module docstring. One instance per run (it knows the run)."""

    txn: TxnFactory
    run_id: UUID
    cfg: SkillConfig

    async def get(self, tenant_id: UUID) -> TenantAgentSettings:
        """The run's per-client settings."""
        async with self.txn(tenant_id) as s:
            current = await tenant_settings.current(s)
            run_input: dict[str, Any] = (
                await s.execute(
                    text("SELECT run_input FROM account_runs WHERE id = :r"), {"r": self.run_id}
                )
            ).scalar_one_or_none() or {}
        frozen = run_input.get("pursuing_client")
        client = frozen if isinstance(frozen, str) and frozen.strip() else None
        off = set(run_input.get("lanes_off") or ())
        lanes = tuple(
            lane
            for lane in current.settings.platform_lanes
            if lane in self.cfg.lanes and lane not in off
        )
        return TenantAgentSettings(
            pursuing_client=client or current.settings.pursuing_client,
            lanes=lanes,
            internal_history=InternalHistorySettings(),
        )
