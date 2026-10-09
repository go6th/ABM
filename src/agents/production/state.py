"""Run-scoped stores and tenant settings for the agent layer (Postgres under RLS).

* ``PgRunRecords`` is the ``RunRecordStore``: sense checks (``abm_sense_checks``, the latest
  binds the build, G6) and distill outputs (``abm_distill_outputs``, insert-only per audience).
* ``SettingsAdapter`` is the ``SettingsPort``: lanes from the tenant's saved settings (only skill
  lanes), the pursuing client frozen in the run's input at start (S16; else the tenant setting),
  and internal history switched off until its connector search has a binding (D-98, OI-ABM-4).

GUARANTEE(test_full_abm_run_grades_through_the_engine_and_tracks_every_row)
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
        """Insert once per run and audience, and project client prose to the append-only report.

        The export reader consumes ``report_sections``; it never reads model output directly. Only
        validated client distill claims with known public fact ids are projected. Narrative rows
        require registered source codes, and hypotheses require a matching confirmation question.
        Replays are idempotent at both stores.
        """
        from agents.schemas import DistillOutput

        if not isinstance(output, DistillOutput):
            raise TypeError("distill output must be schema-validated")
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
            if audience != "client":
                return

            account_id = (
                await s.execute(
                    text("SELECT account_id FROM account_runs WHERE id = :r"),
                    {"r": ctx.run_id},
                )
            ).scalar_one_or_none()
            if account_id is None:
                raise LookupError("run not found for this tenant")

            fact_ids = sorted(
                {
                    fact_id
                    for claim in (*output.narrative, *output.hypotheses, *output.questions)
                    for fact_id in claim.fact_ids
                }
            )
            codes_by_fact: dict[str, set[str]] = {}
            if fact_ids:
                rows = (
                    await s.execute(
                        text(
                            "SELECT DISTINCT f.id::text AS fact_id, src.source_code"
                            " FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
                            " JOIN sources src ON src.id = e.source_id"
                            " WHERE f.run_id = :r AND f.account_id = :a"
                            " AND f.id = ANY(CAST(:ids AS uuid[]))"
                            " AND f.visibility = 'public' AND f.superseded_at IS NULL"
                            " AND e.evidence_kind <> 'search_proof'"
                        ),
                        {"r": ctx.run_id, "a": account_id, "ids": fact_ids},
                    )
                ).all()
                for row in rows:
                    codes_by_fact.setdefault(row.fact_id, set()).add(row.source_code)

            async def project(
                kind: str,
                position: int,
                claim: Any,
                *,
                question: str | None = None,
                require_citation: bool = False,
            ) -> None:
                """Persist one checked section; uncited narrative is withheld from the renderer."""
                ids = sorted(set(claim.fact_ids))
                codes = sorted({code for fid in ids for code in codes_by_fact.get(fid, set())})
                if require_citation and not codes:
                    return
                # A run's client report is immutable. Conflicts are safe replays of the same
                # graph node, not edits or replacement of previously rendered prose.
                await s.execute(
                    text(
                        "INSERT INTO report_sections (tenant_id, account_id, run_id, version,"
                        " kind, position, text, cited_source_codes, fact_ids, confirming_question,"
                        " visibility) VALUES (:t, :a, :r, 1, :k, :p, :body, :codes,"
                        " CAST(:ids AS uuid[]), :question, 'public')"
                        " ON CONFLICT (tenant_id, run_id, version, kind, position) DO NOTHING"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "a": account_id,
                        "r": ctx.run_id,
                        "k": kind,
                        "p": position,
                        "body": claim.text,
                        "codes": codes,
                        "ids": ids,
                        "question": question,
                    },
                )

            for position, claim in enumerate(output.narrative):
                await project("narrative", position, claim, require_citation=True)
            for position, claim in enumerate(output.questions):
                # A question is a prompt for the account owner, not an asserted fact.
                await project("first_call_question", position, claim)
            for position, hypothesis in enumerate(output.hypotheses):
                ids = set(hypothesis.fact_ids)
                confirmation = next(
                    (q for q in output.questions if ids.intersection(q.fact_ids)), None
                )
                if confirmation is not None:
                    await project(
                        "hypothesis",
                        position,
                        hypothesis,
                        question=confirmation.text,
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
