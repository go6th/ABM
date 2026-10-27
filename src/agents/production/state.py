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
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.product import statement_policy
from account_intelligence.grading.repo import TxnFactory
from agents.adapters import PgSenseChecks
from agents.config import SkillConfig
from agents.distill.agent import NARRATIVE_FROM_QUESTIONS
from agents.ports import InternalHistorySettings, RunContext, TenantAgentSettings
from obs.logging import logger
from tenancy import settings as tenant_settings

_log = logger(__name__)
# section kinds whose sentences state facts: one resting on a fact a client file may not state
# is withheld here, as the Summary reader would withhold it (``exports.readers.summary``)
_STATING_KINDS = ("narrative", "stands_out", "lane_meaning", "decision_change")


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
                    for claim in (
                        *output.narrative,
                        *output.hypotheses,
                        *output.questions,
                        *output.stands_out,
                        *output.lane_meanings,
                        *output.could_be_wrong,
                        *output.sharpen,
                    )
                    for fact_id in claim.fact_ids
                }
            )
            codes_by_fact: dict[str, set[str]] = {}
            grade_by_fact: dict[str, str] = {}
            grading = load_grading_config()
            if fact_ids:
                rows = (
                    await s.execute(
                        text(
                            "SELECT DISTINCT f.id::text AS fact_id, f.product_grade,"
                            " src.source_code"
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
                    grade_by_fact[row.fact_id] = str(row.product_grade)

            withheld: Counter[str] = Counter()

            async def project(
                kind: str,
                position: int,
                claim: Any,
                *,
                question: str | None = None,
                require_citation: bool = False,
                lane: str | None = None,
            ) -> bool:
                """Persist one checked section; uncited narrative is withheld from the renderer, and
                so is narrative that states a fact the grading policy does not let a client file
                state (a Lead or Probable is asked about, never asserted). Returns whether the
                section was kept; what was withheld is counted and logged (audit 2026-10-15: the
                Summary failed its build with no trace of why its narrative was empty)."""
                ids = sorted(set(claim.fact_ids))
                codes = sorted({code for fid in ids for code in codes_by_fact.get(fid, set())})
                if require_citation and not codes:
                    withheld[f"{kind}_uncited"] += 1
                    return False
                states = kind in _STATING_KINDS and not claim.text.rstrip().endswith("?")
                if states and any(
                    not statement_policy(grade_by_fact[fid], grading)
                    for fid in ids
                    if fid in grade_by_fact
                ):
                    withheld[f"{kind}_unconfirmed_stated"] += 1
                    return False
                # A run's client report is immutable. Conflicts are safe replays of the same
                # graph node, not edits or replacement of previously rendered prose.
                await s.execute(
                    text(
                        "INSERT INTO report_sections (tenant_id, account_id, run_id, version,"
                        " kind, position, text, cited_source_codes, fact_ids, confirming_question,"
                        " lane, visibility) VALUES (:t, :a, :r, 1, :k, :p, :body, :codes,"
                        " CAST(:ids AS uuid[]), :question, :lane, 'public')"
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
                        "lane": lane,
                    },
                )
                return True

            kept = 0
            for position, claim in enumerate(output.narrative):
                kept += await project("narrative", position, claim, require_citation=True)
            if not kept and output.questions:
                # no narrative sentence survived: the Summary opens with the first-call questions
                # (question-form, the same facts, cited), never with nothing (audit 2026-10-15)
                for position, claim in enumerate(output.questions[:NARRATIVE_FROM_QUESTIONS]):
                    kept += await project(
                        "narrative", len(output.narrative) + position, claim, require_citation=True
                    )
                withheld["narrative_from_questions"] += 1 if kept else 0
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
            # the rest of the Summary's section 1 (the reference Summary, 2026-10-26): what
            # stands out, what it likely means per lane, how this could be wrong, how to sharpen
            for position, claim in enumerate(output.stands_out):
                await project("stands_out", position, claim, require_citation=True)
            for position, meaning in enumerate(output.lane_meanings):
                await project(
                    "lane_meaning", position, meaning, require_citation=True, lane=meaning.lane
                )
            for position, claim in enumerate(output.could_be_wrong):
                await project("caveat", position, claim)
            for position, claim in enumerate(output.sharpen):
                await project("decision_change", position, claim, require_citation=True)
            _log.info(
                "distill.projected",
                extra={"count": kept, "dropped": sum(withheld.values()), "kind": "narrative"},
            )
            if withheld:
                _log.info(
                    "distill.withheld",
                    extra={
                        "error_code": ",".join(f"{k}={v}" for k, v in sorted(withheld.items()))[
                            :200
                        ]
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
            row = (
                await s.execute(
                    text("SELECT run_input, budget_json FROM account_runs WHERE id = :r"),
                    {"r": self.run_id},
                )
            ).first()
            run_input: dict[str, Any] = dict(row.run_input or {}) if row else {}
            # the depth the run was started with (the request's standard or deep)
            requested = dict(row.budget_json or {}).get("depth") if row else None
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
            depth="deep" if requested == "deep" else "standard",
        )
