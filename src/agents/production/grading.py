"""``GradingPort`` on the grading engine and its single write path.

* ``grade`` turns verified candidates into the engine's proposal schema (attribute key
  ``<row>.<attribute>``, evidence URL and verbatim sentence, a confirming question, the declared
  ``match_origin``), grades them with ``grade_claims`` against this run's ledgered sources, and
  records facts, drops and Lead states with ``record_graded_fact``/``record_drops``/
  ``record_lead``. Private candidates are internal-only keys (product grade Internal Only, fact
  visibility private). Nothing here assigns a grade.
* ``InheritedLedger`` is the engine's source and fetch view of a run plus its declared parent
  pull run (a Phase 2 run grades the pull it was built on, G16; same tenant by FK): the parent's
  real records, presented as this run's inputs. Never another account's or tenant's run.
* ``mark_conflicting`` supersedes each named fact with the same claim capped at the engine's
  configured Conflicting maximum (never raised) and flagged ``conflicting``; already-flagged or
  superseded facts are skipped. The write goes through ``repo.supersede``.
* ``record_people`` has no production binding yet (``NotConfigured``, OI-ABM-4).

GUARANTEE(test_full_abm_run_grades_through_the_engine_and_tracks_every_row)
GUARANTEE(test_phase_two_on_a_stored_pull_inherits_its_ledger)
GUARANTEE(test_mark_conflicting_caps_and_never_raises_a_grade)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.grading import repo
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.integration import judge_profile_ok, load_run_info
from account_intelligence.grading.pipeline import _hash, grade_claims, load_inputs
from account_intelligence.grading.ports import FetchRecord, RunInfo, RunSource, SupportJudgePort
from account_intelligence.grading.product import statement_policy
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.grading.schema import GradedClaim, GradedEvidence, LeadRecord
from account_intelligence.platform.ports import Clock, NotConfigured
from agents.ports import GradeBatch, GradingCandidate, RunContext
from agents.schemas import PersonCandidate, StoredFact

MATCHES = frozenset({"keyword_match", "date_range"})
RANK = {"lead": 1, "hypothesis": 2, "probable": 3, "confirmed": 4}


async def parent_run(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> UUID | None:
    """The declared parent pull run of a Phase 2 run (None for full and pull runs)."""
    async with txn(tenant_id) as s:
        value = (
            await s.execute(
                text("SELECT parent_run_id FROM account_runs WHERE id = :r"), {"r": run_id}
            )
        ).scalar_one_or_none()
    return UUID(str(value)) if value is not None else None


@dataclass(frozen=True)
class InheritedLedger:
    """``SourceTextPort`` and ``FetchLedgerPort`` over a run and its declared parent."""

    inner: LedgerAdapter
    txn: TxnFactory

    async def _runs(self, run: RunInfo) -> list[RunInfo]:
        parent = await parent_run(self.txn, run.tenant_id, run.run_id)
        return [run] if parent is None else [run, replace(run, run_id=parent)]

    async def sources(self, run: RunInfo) -> Sequence[RunSource]:
        """This run's sources, then the parent's, presented as this run's."""
        out: list[RunSource] = []
        for r in await self._runs(run):
            out += [replace(s, run_id=run.run_id) for s in await self.inner.sources(r)]
        return out

    async def fetches(self, run: RunInfo) -> Mapping[str, FetchRecord]:
        """This run's fetch records; the parent's fill in references this run has not made."""
        out: dict[str, FetchRecord] = {}
        for r in reversed(await self._runs(run)):
            out.update(
                {k: replace(f, run_id=run.run_id) for k, f in (await self.inner.fetches(r)).items()}
            )
        return out


def _question(attribute: str) -> str:
    return f"What would confirm the {attribute.replace('_', ' ')} for this account?"


def attribute_key(row_key: str, attribute: str) -> str:
    """``<class prefix>.<row>.<attribute>`` for a row with an attribute class, else
    ``<row>.<attribute>`` (the engine refuses it as an unknown attribute class, counted; C38)."""
    from agents.production.provider import load_routes

    prefix = load_routes().attribute_prefix.get(row_key)
    return f"{prefix}.{row_key}.{attribute}" if prefix else f"{row_key}.{attribute}"


def split_key(key: str) -> tuple[str, str]:
    """``(row, attribute)`` of an attribute key written by ``attribute_key``."""
    from agents.production.provider import load_routes

    parts = key.split(".")
    if len(parts) >= 3 and parts[0] in set(load_routes().attribute_prefix.values()):
        return parts[1], ".".join(parts[2:])
    return parts[0], ".".join(parts[1:])


def proposal(index: int, item: GradingCandidate) -> dict[str, Any]:
    """The engine's ``ProposedSourced`` for one candidate (urls aligned with the evidence)."""
    c = item.candidate
    raw: dict[str, Any] = {
        "claim_kind": "sourced",
        "local_id": f"c{index}",
        "attribute_key": attribute_key(c.row_key, c.attribute),
        "value": c.value,
        "evidence": [
            {"url": url, "supporting_sentence": e.quote}
            for url, e in zip(item.urls, c.evidence, strict=True)
        ],
        "confirming_question": _question(c.attribute),
    }
    if c.origin in MATCHES:
        raw["match_origin"] = c.origin
    return raw


def _stored(
    fact_id: UUID | str, claim_key: str, value: str, grade: str, flags: Sequence[str]
) -> StoredFact:
    row, attribute = split_key(claim_key)
    return StoredFact(
        fact_id=str(fact_id),
        row_key=row,
        attribute=attribute,
        value=value,
        grade=grade,
        visibility="private" if grade == "internal" else "public",
        flags=tuple(flags),
    )


@dataclass(frozen=True)
class EngineGrading:
    """See the module docstring."""

    txn: TxnFactory
    ledger: InheritedLedger
    cfg: GradingConfig
    clock: Clock
    # G12: the support judge. Without it (or when the judge would be the generator's model) every
    # claim that needs a judge, Confirmed included, is capped and flagged ``unjudged``.
    judge: SupportJudgePort | None = None

    async def grade(self, ctx: RunContext, items: Sequence[GradingCandidate]) -> GradeBatch:
        """Grade and record a batch; every item ends as a fact or a counted drop."""
        if not items:
            return GradeBatch((), (), {})
        run = await load_run_info(self.txn, ctx.tenant_id, ctx.run_id)
        await repo.pin_config(self.txn, run, self.cfg.content_hash)
        inputs = await load_inputs(run, self.ledger, self.ledger)
        raws = [proposal(i, item) for i, item in enumerate(items)]
        private = frozenset(
            r["attribute_key"]
            for r, i in zip(raws, items, strict=True)
            if i.visibility == "private"
        )
        result = await grade_claims(
            raws,
            run,
            inputs,
            self.cfg,
            self.clock.now(),
            internal_only_keys=private,
            judge=self.judge,
            judge_profile_ok=judge_profile_ok(self.cfg),
        )
        by_hash = {_hash(r): i for i, r in enumerate(raws)}
        facts, index, read = [], [], []
        for claim in result.graded:
            fact_id = await repo.record_graded_fact(self.txn, run, claim)
            facts.append(
                _stored(fact_id, claim.attribute_key, claim.value, claim.product_grade, claim.flags)
            )
            index.append(by_hash[claim.claim_hash])
            read.append(claim.read_in_primary_this_run)
        await repo.record_drops(self.txn, run, result.dropped)
        for lead in result.leads.values():
            await repo.record_lead(self.txn, run, lead)
        return GradeBatch(tuple(facts), tuple(index), dict(result.drop_counts), tuple(read))

    async def facts(self, ctx: RunContext) -> tuple[StoredFact, ...]:
        """The run's current facts (not superseded), ordered by id."""
        async with self.txn(ctx.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, attribute_key, value_text, product_grade, flags FROM facts"
                        " WHERE run_id = :r AND superseded_at IS NULL ORDER BY id"
                    ),
                    {"r": ctx.run_id},
                )
            ).all()
        return tuple(
            _stored(r.id, r.attribute_key, r.value_text or "", r.product_grade, r.flags or ())
            for r in rows
        )

    async def mark_conflicting(
        self, ctx: RunContext, fact_ids: Sequence[str], reason: str
    ) -> tuple[str, ...]:
        """Supersede each fact as Conflicting (capped, flagged); skip flagged or superseded."""
        run = await load_run_info(self.txn, ctx.tenant_id, ctx.run_id)
        out = []
        for fact_id in fact_ids:
            claim = await self._claim(ctx, UUID(fact_id))
            if claim is None or "conflicting" in claim.flags:
                continue
            cap = self.cfg.precedence.conflicting_max_product_grade
            grade = claim.product_grade
            if grade in RANK and RANK[grade] > RANK[cap]:
                grade = cap
            new = claim.model_copy(
                update={
                    "claim_hash": hashlib.sha256(
                        f"{claim.claim_hash}:conflicting:{reason}".encode()
                    ).hexdigest(),
                    "product_grade": grade,
                    "may_state_to_prospect": statement_policy(grade, self.cfg),
                    "precedence_rule": "conflicting",
                    "flags": tuple(sorted({*claim.flags, "conflicting"})),
                    "reasoning": claim.reasoning or "Sources disagree on this value.",
                    "confirming_question": claim.confirming_question
                    or _question(split_key(claim.attribute_key)[1]),
                }
            )
            new = GradedClaim.model_validate_json(new.model_dump_json())  # every rule re-checked
            new_id = await repo.supersede(
                self.txn, run, UUID(fact_id), new, reason_code="sense_check_contradiction"
            )
            out.append(str(new_id))
        return tuple(out)

    async def _claim(self, ctx: RunContext, fact_id: UUID) -> GradedClaim | None:
        async with self.txn(ctx.tenant_id) as s:
            f = (
                await s.execute(
                    text(
                        "SELECT * FROM facts WHERE id = :f AND run_id = :r AND superseded_at IS"
                        " NULL"
                    ),
                    {"f": fact_id, "r": ctx.run_id},
                )
            ).first()
            if f is None:
                return None
            ev = (
                await s.execute(
                    text("SELECT * FROM fact_evidence WHERE fact_id = :f ORDER BY id"),
                    {"f": fact_id},
                )
            ).all()
        value = json.loads(json.dumps(f.value_json)) if f.value_json else {}
        return GradedClaim(
            schema_version=f.schema_version,
            config_hash=f.config_hash,
            claim_hash=f.claim_hash,
            claim_kind=f.claim_kind,
            attribute_key=f.attribute_key,
            value=f.value_text,
            normalised_value=str(value.get("normalised", f.value_text)),
            product_grade=f.product_grade,
            engine_grade=f.engine_grade,
            confidence=float(f.confidence) if f.confidence is not None else None,
            read_in_primary_this_run=bool(f.read_in_primary_this_run),
            may_state_to_prospect=bool(f.may_state_to_prospect),
            precedence_rule=f.precedence_rule,
            evidence=tuple(
                GradedEvidence(
                    evidence_kind=e.evidence_kind,
                    url=e.url,
                    supporting_sentence=e.supporting_sentence,
                    span_sha256=e.span_sha256,
                    source_ref=e.source_ref,
                    call_ref=e.call_ref,
                    origin=e.origin,
                    tier=e.source_tier,
                    lineage_group=e.lineage_group,
                    is_aggregator=bool(e.is_aggregator),
                    date_kind=e.date_kind,
                    evidence_date=e.evidence_date,
                    provider_code=e.provider_code,
                )
                for e in ev
            ),
            confirming_question=f.confirming_question,
            reasoning=f.reasoning,
            flags=tuple(f.flags or ()),
            lead=(
                LeadRecord(
                    status=f.lead_status or "pending",
                    reason=None,
                    accepted_queries=0,
                    tool_categories=(),
                )
                if f.product_grade == "lead"
                else None
            ),
        )

    async def record_people(
        self, ctx: RunContext, people: Sequence[PersonCandidate]
    ) -> tuple[str, ...]:
        """Not bound yet: the people pipeline's repository takes whole pipeline results."""
        raise NotConfigured("agent people recording has no production adapter (OI-ABM-4)")
