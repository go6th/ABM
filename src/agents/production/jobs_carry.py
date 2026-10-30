"""Carrying the previous research's graded job findings into an incremental research.

An incremental research (``jobs.incremental``) carries the previous research's postings without
pulling or reading them again. Their findings (one graded fact per classified posting and named
platform) cannot be graded again: the grading engine verifies a claim only against the sources
THIS run's ledger returned (``grading.pipeline.load_inputs``, ``spans.verify_url``), and a carried
posting was not fetched in this run, so every re-templated claim would end as ``url_not_in_run``.
Without this step the run's facts, which the Summary, the Evidence file and the lane tables read,
would hold only the postings of the delta while the Jobs file lists them all (review 2026-10-29,
finding 3).

Each carried claim is instead matched to the previous run's CURRENT graded fact for the same
attribute, value and address, and that fact is recorded again for this run through the grading
engine's one write path (``grading.repo.record_graded_fact``): same grade, same evidence, same
lane and event date, the sentence it was verified against unchanged. Nothing is graded again, no
grade is raised, and "facts keep one writer" holds. A fact graded under another grading
configuration is not carried (the run is graded with one configuration); the claim is counted in
the second return value so the gap is visible, never silently lost.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.grading import repo
from account_intelligence.grading.ports import RunInfo
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.grading.schema import GradedClaim, GradedEvidence, LeadRecord


def _address(url: str) -> str:
    """The comparable form of an evidence address (the grading engine's own normalisation)."""
    from account_intelligence.urls import normalize_url

    try:
        return str(normalize_url(url))
    except ValueError:
        return url.strip()


def _graded(f: Any, evidence: Sequence[Any]) -> GradedClaim:
    """The graded claim a stored fact and its evidence rows record."""
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
            for e in evidence
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


async def carry_job_facts(
    txn: TxnFactory,
    run: RunInfo,
    previous: UUID,
    claims: Sequence[Mapping[str, Any]],
    config_hash: str,
) -> tuple[set[str], set[str]]:
    """Record, for ``run``, the previous run's graded fact behind each carried claim.

    Returns ``(carried, unmatched)``: the ``local_id`` of each claim whose fact was carried, and of
    each claim with no fact of the previous run to carry (never graded there, superseded, or graded
    under another configuration)."""
    await repo.pin_config(txn, run, config_hash)
    async with txn(run.tenant_id) as s:
        facts = (
            await s.execute(
                text(
                    "SELECT * FROM facts WHERE run_id = :p AND account_id = :a"
                    " AND superseded_at IS NULL AND attribute_key LIKE 'jobs.%'"
                    " AND config_hash = :h ORDER BY id"
                ),
                {"p": previous, "a": run.account_id, "h": config_hash},
            )
        ).all()
        evidence = (
            await s.execute(
                text("SELECT * FROM fact_evidence WHERE fact_id = ANY(:ids) ORDER BY id"),
                {"ids": [f.id for f in facts]},
            )
        ).all()
    by_fact: dict[Any, list[Any]] = {}
    for e in evidence:
        by_fact.setdefault(e.fact_id, []).append(e)
    available: dict[tuple[str, str, str], Any] = {}
    for f in facts:
        for e in by_fact.get(f.id, ()):
            if e.url:
                available.setdefault((f.attribute_key, f.value_text, _address(e.url)), f)
    carried: set[str] = set()
    unmatched: set[str] = set()
    for claim in claims:
        local_id = str(claim.get("local_id") or "")
        keys = (
            (str(claim.get("attribute_key")), str(claim.get("value")), _address(str(e.get("url"))))
            for e in claim.get("evidence") or ()
        )
        fact = next((available[k] for k in keys if k in available), None)
        if fact is None:
            unmatched.add(local_id)
            continue
        event_date = (json.loads(json.dumps(fact.value_json or {}))).get("event_date")
        await repo.record_graded_fact(
            txn,
            run,
            _graded(fact, by_fact.get(fact.id, ())),
            lane=fact.lane,
            event_date=str(event_date) if event_date else None,
        )
        carried.add(local_id)
    return carried, unmatched
