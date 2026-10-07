"""The only write path for graded facts (G15).

``record_graded_fact`` inserts the fact, its evidence rows and an initial grade event in one
``tenant_txn``; the database refuses a Confirmed without a primary read, a stated non-Confirmed
and a fact without evidence at commit. It is idempotent: the same graded content for the same
run writes once (``write_key``), so a replay after a crash writes no duplicate.
``supersede`` writes a new fact, marks the old one superseded and logs a grade event; the old
value is never updated. Drops and Lead verification snapshots are insert-only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.grading.leads import LeadVerification
from account_intelligence.grading.pipeline import Dropped
from account_intelligence.grading.ports import RunInfo
from account_intelligence.grading.schema import GradedClaim

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def fingerprint(account_id: UUID, attribute_key: str, normalised_value: str) -> str:
    """hash(entity, attribute_key, normalised value): equal facts collide (skill reference 06)."""
    blob = json.dumps([str(account_id), attribute_key, normalised_value], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_key(run: RunInfo, claim: GradedClaim) -> str:
    """Idempotency key of a graded fact (a replay after a crash writes no duplicate)."""
    blob = json.dumps(
        [str(run.run_id), claim.model_dump(mode="json")], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _insert_fact(s: AsyncSession, run: RunInfo, claim: GradedClaim) -> tuple[UUID, bool]:
    key = write_key(run, claim)
    row = (
        await s.execute(
            text(
                "INSERT INTO facts (account_id, tenant_id, attribute_key, value_json, fingerprint,"
                " engine_grade, product_grade, confidence, read_in_primary_this_run, visibility,"
                " run_id, claim_hash, write_key, claim_kind, value_text, confirming_question,"
                " reasoning,"
                " flags, precedence_rule, config_hash, schema_version, lead_status,"
                " may_state_to_prospect)"
                " VALUES (:account, (SELECT app_tenant()), :key, CAST(:value_json AS jsonb), :fp,"
                " :eg, :pg,"
                " :conf, :prim, :vis, :run, :ch, :wk, :kind, :value, :q, :reasoning, CAST(:flags"
                " AS jsonb),"
                " :rule, :cfg, :sv, :lead, :may)"
                " ON CONFLICT (tenant_id, write_key) WHERE write_key IS NOT NULL DO NOTHING"
                " RETURNING id"
            ),
            {
                "account": run.account_id,
                "key": claim.attribute_key,
                "value_json": json.dumps(
                    {"value": claim.value, "normalised": claim.normalised_value}
                ),
                "fp": fingerprint(run.account_id, claim.attribute_key, claim.normalised_value),
                "eg": claim.engine_grade,
                "pg": claim.product_grade,
                "conf": None if claim.confidence is None else round(claim.confidence, 4),
                "prim": claim.read_in_primary_this_run,
                "vis": "private" if claim.product_grade == "internal" else "public",
                "run": run.run_id,
                "ch": claim.claim_hash,
                "wk": key,
                "kind": claim.claim_kind,
                "value": claim.value,
                "q": claim.confirming_question,
                "reasoning": claim.reasoning,
                "flags": json.dumps(list(claim.flags)),
                "rule": claim.precedence_rule,
                "cfg": claim.config_hash,
                "sv": claim.schema_version,
                "lead": claim.lead.status if claim.lead else None,
                "may": claim.may_state_to_prospect,
            },
        )
    ).first()
    if row is not None:
        return UUID(str(row.id)), True
    existing: Any = (
        await s.execute(text("SELECT id FROM facts WHERE write_key = :wk"), {"wk": key})
    ).scalar_one()
    return UUID(str(existing)), False


async def _insert_evidence(s: AsyncSession, fact_id: UUID, claim: GradedClaim) -> None:
    for e in claim.evidence:
        await s.execute(
            text(
                "INSERT INTO fact_evidence (fact_id, evidence_kind, url, supporting_sentence,"
                " span_sha256,"
                " source_ref, call_ref, origin, source_tier, lineage_group, is_aggregator,"
                " date_kind,"
                " evidence_date, provider_code) VALUES (:f, :k, :u, :s, :h, :sr, :cr, :o, :t,"
                " :lg, :ag,"
                " :dk, :ed, :pc)"
            ),
            {
                "f": fact_id,
                "k": e.evidence_kind,
                "u": e.url,
                "s": e.supporting_sentence,
                "h": e.span_sha256,
                "sr": e.source_ref,
                "cr": e.call_ref,
                "o": e.origin,
                "t": e.tier,
                "lg": e.lineage_group,
                "ag": e.is_aggregator,
                "dk": e.date_kind,
                "ed": e.evidence_date,
                "pc": e.provider_code,
            },
        )


async def _grade_event(
    s: AsyncSession,
    fact_id: UUID,
    run: RunInfo,
    claim: GradedClaim,
    actor: str,
    reason: str,
    from_product: str | None = None,
    from_engine: str | None = None,
) -> None:
    await s.execute(
        text(
            "INSERT INTO grade_events (fact_id, run_id, from_product, to_product, from_engine,"
            " to_engine,"
            " actor, reason_code) VALUES (:f, :r, :fp, :tp, :fe, :te, :a, :rc)"
        ),
        {
            "f": fact_id,
            "r": run.run_id,
            "fp": from_product,
            "tp": claim.product_grade,
            "fe": from_engine,
            "te": claim.engine_grade,
            "a": actor,
            "rc": reason,
        },
    )


async def record_graded_fact(
    txn: TxnFactory, run: RunInfo, claim: GradedClaim, *, actor: str = "grading_engine"
) -> UUID:
    """Insert fact, evidence and an initial grade event atomically; idempotent per content."""
    async with txn(run.tenant_id) as s:
        fact_id, created = await _insert_fact(s, run, claim)
        if created:
            await _insert_evidence(s, fact_id, claim)
            await _grade_event(s, fact_id, run, claim, actor, "initial")
        return fact_id


async def supersede(
    txn: TxnFactory,
    run: RunInfo,
    old_fact_id: UUID,
    claim: GradedClaim,
    *,
    reason_code: str,
    actor: str = "grading_engine",
) -> UUID:
    """A later grade (e.g. a judge downgrade or a Lead upgrade re-grade) is a new fact."""
    async with txn(run.tenant_id) as s:
        old = (
            await s.execute(
                text(
                    "SELECT product_grade, engine_grade FROM facts WHERE id = :id AND"
                    " superseded_at IS NULL FOR UPDATE"
                ),
                {"id": old_fact_id},
            )
        ).first()
        if old is None:
            raise LookupError("fact not found or already superseded")
        new_id, created = await _insert_fact(s, run, claim)
        if created:
            await _insert_evidence(s, new_id, claim)
        await s.execute(
            text("UPDATE facts SET superseded_at = now(), superseded_by = :n WHERE id = :o"),
            {"n": new_id, "o": old_fact_id},
        )
        await _grade_event(
            s, new_id, run, claim, actor, reason_code, old.product_grade, old.engine_grade
        )
        return new_id


async def pin_config(txn: TxnFactory, run: RunInfo, config_hash: str) -> str:
    """Pin the grading config hash on the run once; return the pinned hash. A later call with a
    different hash leaves the first one (a run is always graded with one configuration)."""
    async with txn(run.tenant_id) as s:
        await s.execute(
            text(
                "UPDATE account_runs SET grading_config_hash = :h WHERE id = :r AND"
                " grading_config_hash IS NULL"
            ),
            {"h": config_hash, "r": run.run_id},
        )
        pinned = (
            await s.execute(
                text("SELECT grading_config_hash FROM account_runs WHERE id = :r"),
                {"r": run.run_id},
            )
        ).scalar_one_or_none()
    if pinned is None:
        raise LookupError("run not found for this tenant")
    return str(pinned)


async def record_drops(txn: TxnFactory, run: RunInfo, drops: Sequence[Dropped]) -> int:
    """Every drop is counted (insert-only, idempotent per run, claim and reason)."""
    written = 0
    async with txn(run.tenant_id) as s:
        for d in drops:
            res = await s.execute(
                text(
                    "INSERT INTO claim_drops (run_id, reason_code, attribute_key, claim_hash,"
                    " excerpt, hostile_url)"
                    " VALUES (:r, :rc, :ak, :ch, :ex, :hu)"
                    " ON CONFLICT (tenant_id, run_id, claim_hash, reason_code) DO NOTHING"
                ),
                {
                    "r": run.run_id,
                    "rc": d.reason,
                    "ak": d.attribute_key,
                    "ch": d.claim_hash,
                    "ex": d.excerpt[:200],
                    "hu": d.hostile_url,
                },
            )
            written += res.rowcount  # type: ignore[attr-defined]
    return written


async def record_lead(txn: TxnFactory, run: RunInfo, state: LeadVerification) -> UUID:
    """Append a snapshot of a Lead's verification state and its queries."""
    async with txn(run.tenant_id) as s:
        lead_id: Any = (
            await s.execute(
                text(
                    "INSERT INTO lead_verifications (run_id, lead_key, status, reason, exit_reason,"
                    " accepted_count, categories, repasses_used) VALUES (:r, :k, :st, :re, :ex,"
                    " :n, :c, :rp)"
                    " RETURNING id"
                ),
                {
                    "r": run.run_id,
                    "k": state.lead_key,
                    "st": state.status,
                    "re": state.reason,
                    "ex": state.exit_reason,
                    "n": len(state.accepted),
                    "c": sorted(state.categories),
                    "rp": state.repasses_used,
                },
            )
        ).scalar_one()
        for seq, q in enumerate(state.queries, start=1):
            await s.execute(
                text(
                    "INSERT INTO lead_verification_queries (lead_verification_id, seq, query_text,"
                    " tool_category, call_ref, accepted, rejection_reason) VALUES (:l, :s, :q,"
                    " :c, :cr, :a, :rr)"
                ),
                {
                    "l": lead_id,
                    "s": seq,
                    "q": q.text,
                    "c": q.tool_category,
                    "cr": q.call_ref,
                    "a": q.accepted,
                    "rr": q.rejection_reason,
                },
            )
        return UUID(str(lead_id))


async def expire(s: AsyncSession, fact_id: UUID, now: datetime) -> UUID | None:
    """Mark a current fact expired (``superseded_at`` set once, no successor: a signal past its
    shelf life, Phase 4 RF2); returns its account id, or None when it was already superseded.
    The caller records why (``refresh.history``). The only expiry path for facts."""
    row = (
        await s.execute(
            text(
                "UPDATE facts SET superseded_at = :now WHERE id = :f AND superseded_at IS NULL"
                " RETURNING account_id"
            ),
            {"now": now, "f": fact_id},
        )
    ).first()
    return None if row is None else UUID(str(row.account_id))
