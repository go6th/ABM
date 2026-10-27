"""Integration with Workflow v1: the ``distill_grade`` stage handler and the ``FactRepo`` port.

* ``load_run_info`` builds ``RunInfo`` from the database under the run's tenant: account domain,
  name, the verified careers host (a run-derived primary host), and the run start.
* ``make_distill_grade_handler`` returns a stage handler: it loads the run's proposals by reference
  (never through the checkpoint), grades them with ``grade_claims``, pins the config hash, and
  persists facts, drops and Lead states. It returns only counters and loop exits: no claim text
  or page text enters the workflow state.
* ``GradingFactRepo`` implements the workflow ``FactRepo``: it only accepts a validated
  ``GradedClaim`` (a dict that is not one is refused), through ``record_graded_fact``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from account_intelligence.grading import repo
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.errors import ConfigError
from account_intelligence.grading.judge import ensure_judge_differs, model_profile
from account_intelligence.grading.pipeline import grade_claims, load_inputs
from account_intelligence.grading.ports import (
    Clock,
    FetchLedgerPort,
    RunInfo,
    SourceTextPort,
    SupportJudgePort,
)
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.grading.schema import GradedClaim


class ProposalSource(Protocol):
    """Where the extractor's proposals are loaded from (by reference, never the checkpoint)."""

    async def load(
        self, tenant_id: UUID, run_id: UUID, reference: str
    ) -> Sequence[str | Mapping[str, Any]]:
        """The extractor's proposals for this run, from its store (never the checkpoint)."""
        ...


async def load_run_info(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> RunInfo:
    """RunInfo from the database under the run's tenant (domain, name, verified careers host)."""
    async with txn(tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT r.id, r.account_id, r.created_at, a.domain, a.name, a.careers_url,"
                    " a.careers_url_verified_at, a.entity_lock FROM account_runs r JOIN accounts a"
                    " ON a.id = r.account_id AND a.tenant_id = r.tenant_id WHERE r.id = :r"
                ),
                {"r": run_id},
            )
        ).first()
    if row is None:
        raise LookupError("run not found for this tenant")
    hosts: frozenset[str] = frozenset()
    if row.careers_url and row.careers_url_verified_at is not None:
        host = (urlsplit(row.careers_url).hostname or "").lower()
        hosts = frozenset({host}) if host else frozenset()
    lock = row.entity_lock if isinstance(row.entity_lock, dict) else {}
    allowed = frozenset(
        str(d).lower() for d in (lock.get("allowed_domains") or ()) if str(d).strip()
    )
    return RunInfo(
        tenant_id,
        UUID(str(row.account_id)),
        run_id,
        row.created_at,
        row.domain,
        row.name,
        hosts,
        allowed_domains=allowed,
    )


def judge_profile_ok(cfg: GradingConfig) -> bool:
    """True only when the configured judge model differs from the generator (G12)."""
    try:
        ensure_judge_differs(
            cfg, model_profile(cfg.judge.judge_task), model_profile(cfg.judge.generator_task)
        )
    except ConfigError:
        return False
    return True


@dataclass(frozen=True)
class DistillGradeDeps:
    """Dependencies of the standalone distill_grade handler."""

    txn: TxnFactory
    sources: SourceTextPort
    ledger: FetchLedgerPort
    proposals: ProposalSource
    cfg: GradingConfig
    clock: Clock
    judge: SupportJudgePort | None = None
    proposals_reference_key: str = "claim_proposals"


def make_distill_grade_handler(deps: DistillGradeDeps) -> Any:
    """A distill_grade stage handler over one proposals reference."""

    async def handler(state: Mapping[str, Any]) -> dict[str, Any]:
        """Grade the run's proposals and persist facts, drops and Lead states."""
        tenant_id, run_id = UUID(str(state["tenant_id"])), UUID(str(state["run_id"]))
        reference = (state.get("references") or {}).get(deps.proposals_reference_key)
        if reference is None:
            return {
                "coverage_updates": {},
                "counters": {"claims_graded": 0, "claims_dropped": 0},
                "loop_exits": {"distill_grade": "no_progress"},
            }
        run = await load_run_info(deps.txn, tenant_id, run_id)
        await repo.pin_config(deps.txn, run, deps.cfg.content_hash)
        inputs = await load_inputs(run, deps.sources, deps.ledger)
        proposals = await deps.proposals.load(tenant_id, run_id, reference)
        result = await grade_claims(
            proposals,
            run,
            inputs,
            deps.cfg,
            deps.clock.now(),
            judge=deps.judge,
            judge_profile_ok=deps.judge is not None and judge_profile_ok(deps.cfg),
        )
        for claim in result.graded:
            await repo.record_graded_fact(deps.txn, run, claim)
        await repo.record_drops(deps.txn, run, result.dropped)
        for lead in result.leads.values():
            await repo.record_lead(deps.txn, run, lead)
        counters = {"claims_graded": len(result.graded), "claims_dropped": len(result.dropped)}
        counters.update({f"grade_{k}": v for k, v in result.grade_counts.items()})
        counters.update({f"drop_{k}": v for k, v in result.drop_counts.items()})
        return {
            "coverage_updates": {},
            "counters": counters,
            "loop_exits": {"distill_grade": "converged"},
        }

    return handler


@dataclass(frozen=True)
class GradingFactRepo:
    """Workflow ``FactRepo`` over the single grading write path."""

    txn: TxnFactory

    async def write_with_evidence(self, tenant_id: str, run_id: str, fact: dict[str, Any]) -> str:
        """Persist one validated GradedClaim through the single write path."""
        # JSON-mode validation: the workflow passes JSON-shaped dicts; every rule still applies.
        claim = GradedClaim.model_validate_json(json.dumps(fact, default=str))
        run = await load_run_info(self.txn, UUID(tenant_id), UUID(run_id))
        return str(await repo.record_graded_fact(self.txn, run, claim))

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        """The run's current facts."""
        async with self.txn(UUID(tenant_id)) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, attribute_key, value_text, product_grade, engine_grade,"
                        " lead_status,"
                        " may_state_to_prospect, superseded_at FROM facts WHERE run_id = :r ORDER"
                        " BY id"
                    ),
                    {"r": UUID(run_id)},
                )
            ).all()
        return [dict(r._mapping) | {"id": str(r.id)} for r in rows]
