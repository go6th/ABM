"""``grade_claims``: guard, engine, product grade, Lead rule and judge for a batch of proposals.

Per-claim isolation: every proposal ends as a ``GradedClaim`` or a ``Dropped(reason)``; one
claim's failure never aborts the batch, and drops are always counted. Output is sorted by
claim hash, so the same proposals in any order give byte-identical results (G10).
Persistence is separate (``repo.record_graded_fact``, ``repo.record_drop``).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from account_intelligence.grading import obs
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.engine import (
    AttributeGrade,
    AttributeStatus,
    DateKind,
    EngineEvidence,
    ValueScore,
    attribute_class,
    grade_attribute,
    normaliser_for,
    score_value,
)
from account_intelligence.grading.errors import GradingError, SchemaViolation, require_aware
from account_intelligence.grading.judge import (
    Judged,
    apply_support_verdict,
    call_judge_many,
    needs_judge,
)
from account_intelligence.grading.leads import LeadVerification, evaluate_lead, new_lead
from account_intelligence.grading.lineage import profile_for
from account_intelligence.grading.ports import (
    FetchLedgerPort,
    FetchRecord,
    RunInfo,
    RunSource,
    SourceTextPort,
    SupportJudgePort,
)
from account_intelligence.grading.primary import VerifiedEvidence, read_in_primary_this_run
from account_intelligence.grading.product import ProductInputs, product_grade, statement_policy
from account_intelligence.grading.schema import (
    PROPOSED_ADAPTER,
    SCHEMA_VERSION,
    GradedClaim,
    GradedEvidence,
    LeadRecord,
    ProposedAbsence,
    ProposedSourced,
    ProposedSynthesis,
)
from account_intelligence.grading.spans import (
    bounded,
    check_hostile,
    normalise_value,
    normalise_ws,
    sanitise,
    value_support,
    verify_span,
    verify_url,
)
from crosscut.fanout import fan_out_all


@dataclass(frozen=True)
class Dropped:
    """A claim that did not survive, with its counted reason."""

    reason: str
    claim_hash: str
    local_id: str | None
    attribute_key: str | None
    excerpt: str
    hostile_url: str | None = None


@dataclass(frozen=True)
class BatchResult:
    """A graded batch: claims, drops with counts, grade counts and Lead states."""

    graded: tuple[GradedClaim, ...]
    dropped: tuple[Dropped, ...]
    drop_counts: Mapping[str, int]
    grade_counts: Mapping[str, int]
    hostile_urls: tuple[str, ...]
    leads: Mapping[str, LeadVerification] = field(default_factory=dict)

    def output_hash(self) -> str:
        """Hash of the graded output (determinism checks)."""
        blob = json.dumps(
            {
                "graded": [g.model_dump(mode="json") for g in self.graded],
                "dropped": [d.__dict__ for d in self.dropped],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class GradingInputs:
    """Snapshot of this run's stored sources and ledger (loaded once per batch)."""

    sources: tuple[RunSource, ...]
    fetches: Mapping[str, FetchRecord]


async def load_inputs(
    run: RunInfo, sources: SourceTextPort, ledger: FetchLedgerPort
) -> GradingInputs:
    """This run's stored sources and fetch records (loaded once per batch)."""
    loaded = tuple(s for s in await sources.sources(run) if s.run_id == run.run_id)
    fetches = {k: f for k, f in (await ledger.fetches(run)).items() if f.run_id == run.run_id}
    return GradingInputs(loaded, fetches)


class _Drop(Exception):
    def __init__(self, reason: str, hostile_url: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.hostile_url = hostile_url


def _hash(raw: Any) -> str:
    text = (
        raw
        if isinstance(raw, str)
        else json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse(raw: str | Mapping[str, Any]) -> ProposedSourced | ProposedSynthesis | ProposedAbsence:
    text = raw if isinstance(raw, str) else json.dumps(raw)
    try:
        return PROPOSED_ADAPTER.validate_json(text)
    except ValidationError as error:
        raise _Drop("schema_violation") from error


@dataclass
class _Sourced:
    proposal: ProposedSourced
    claim_hash: str
    norm_value: str
    engine: list[EngineEvidence]
    evidence: list[GradedEvidence]
    verified: list[VerifiedEvidence]
    secondary_only: bool
    flags: set[str]
    aggregator_only: bool = True


def _clean(p: ProposedSourced | ProposedSynthesis | ProposedAbsence, cfg: GradingConfig) -> None:
    s = cfg.spans
    texts = [
        p.value,
        getattr(p, "reasoning", None) or "",
        getattr(p, "confirming_question", None) or "",
    ]
    for t in texts:
        check_hostile(t, cfg)
    bounded(p.value, s.max_value_chars, "value")
    reasoning: str | None = getattr(p, "reasoning", None)
    question: str | None = getattr(p, "confirming_question", None)
    if reasoning:
        bounded(reasoning, s.max_reasoning_chars, "reasoning")
    if question:
        bounded(question, s.max_question_chars, "confirming question")


def _evidence_date(source: RunSource, live_state: bool) -> tuple[datetime, DateKind]:
    if live_state:
        return source.fetched_at, "observed"
    if source.published_at is not None:
        return source.published_at, "published"
    return source.fetched_at, "unknown"


def _check_sourced(
    p: ProposedSourced, claim_hash: str, run: RunInfo, inputs: GradingInputs, cfg: GradingConfig
) -> _Sourced:
    _clean(p, cfg)
    attribute_class(p.attribute_key, cfg)
    norm_kind = normaliser_for(p.attribute_key, cfg)
    norm_value = normalise_value(p.value, norm_kind)
    out = _Sourced(p, claim_hash, norm_value, [], [], [], True, set())
    for item in p.evidence:
        try:
            check_hostile(item.supporting_sentence, cfg)
        except GradingError as error:
            raise _Drop(error.reason_code, hostile_url=item.url) from error
        url, candidates = verify_url(item.url, inputs.sources)
        source = verify_span(item.supporting_sentence, candidates, cfg)
        sentence = normalise_ws(item.supporting_sentence)
        value_support(p.value, sentence, norm_kind)
        profile = profile_for(source, run, cfg)
        when, kind = _evidence_date(source, profile.live_state)
        require_aware(when, "evidence date")
        out.flags.update(profile.flags)
        if kind == "unknown":
            out.flags.add("date_unknown")
        if profile.tier == "T1" and not profile.is_aggregator:
            out.secondary_only = False
        if not profile.is_aggregator:
            out.aggregator_only = False
        out.engine.append(
            EngineEvidence(
                profile.tier, profile.lineage_group, when, kind, norm_value, profile.lineage_known
            )
        )
        out.evidence.append(
            GradedEvidence(
                evidence_kind="source",
                url=url,
                supporting_sentence=sentence,
                span_sha256=hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
                source_ref=source.source_ref,
                call_ref=source.call_ref,
                origin=source.origin,
                tier=profile.tier,
                lineage_group=profile.lineage_group,
                is_aggregator=profile.is_aggregator,
                date_kind=kind,
                evidence_date=when,
                provider_code=source.provider_code,
            )
        )
        out.verified.append(
            VerifiedEvidence(url, profile.tier, source.call_ref, source.url_normalized, True)
        )
    return out


def _reasoning(score: ValueScore | None, prim: bool, given: str | None) -> str:
    if given:
        return given
    if score is None:
        return "Graded by the evidence engine."
    return (
        f"Supported by {len(score.groups)} independent source group(s) at confidence "
        f"{float(score.confidence):.2f}; {'read' if prim else 'not read'} in a primary source"
        f" this run."
    )


def _drop_reason(error: ValidationError) -> str:
    text = str(error)
    if "confirming question" in text:
        return "missing_confirming_question"
    if "reasoning" in text:
        return "missing_reasoning"
    return "schema_violation"


# verdicts asked at once; each is a short standard call
JUDGE_CONCURRENCY = 12


@dataclass(frozen=True)
class _Pending:
    """A claim built and waiting for its judge's verdict, with what applying the verdict needs."""

    claim: GradedClaim
    sourced: _Sourced
    proposal: ProposedSourced
    score: Any
    read_in_primary: bool
    rule: str


async def _judge_all(
    claim: GradedClaim, port: SupportJudgePort | None, judge_ok: bool, cfg: GradingConfig
) -> Judged:
    if not needs_judge(claim.engine_grade or "D", claim.product_grade, cfg):
        return Judged(True, claim.engine_grade or "D", claim.product_grade, claim.flags)
    statement = f"{claim.attribute_key} = {claim.value}"
    # every distinct evidence sentence, in order, judged in one call (``call_judge_many``)
    sentences = list(dict.fromkeys(ev.supporting_sentence for ev in claim.evidence))
    result = await call_judge_many(port if judge_ok else None, statement, sentences, cfg)
    return apply_support_verdict(
        claim.engine_grade or "D", claim.product_grade, claim.flags, result, cfg
    )


async def grade_claims(
    proposals: Sequence[str | Mapping[str, Any]],
    run: RunInfo,
    inputs: GradingInputs,
    cfg: GradingConfig,
    now: datetime,
    *,
    judge: SupportJudgePort | None = None,
    judge_profile_ok: bool = False,
    lead_states: Mapping[str, LeadVerification] | None = None,
    blocked: Mapping[str, str] | None = None,
    internal_only_keys: frozenset[str] = frozenset(),
) -> BatchResult:
    """Grade a batch. ``judge_profile_ok`` must come from ``judge.ensure_judge_differs``;
    without it every claim that needs a judge is capped and flagged ``unjudged``."""
    require_aware(now, "now")
    lead_states = lead_states or {}
    blocked = blocked or {}
    dropped: list[Dropped] = []
    sourced: list[_Sourced] = []
    others: list[tuple[str, ProposedSynthesis | ProposedAbsence]] = []
    hostile: set[str] = set()

    def drop(
        reason: str, h: str, p: Any = None, raw: Any = None, hostile_url: str | None = None
    ) -> None:
        """Record one dropped claim with its counted reason and a sanitised excerpt."""
        excerpt_src = getattr(p, "value", None) or (
            raw if isinstance(raw, str) else json.dumps(raw, default=str)
        )
        dropped.append(
            Dropped(
                reason,
                h,
                getattr(p, "local_id", None),
                getattr(p, "attribute_key", None),
                sanitise(str(excerpt_src), cfg.spans.max_excerpt_chars),
                hostile_url,
            )
        )
        if hostile_url:
            hostile.add(hostile_url)

    with obs.batch_span(run.run_id, len(proposals)):
        for raw in proposals:
            h = _hash(raw)
            try:
                p = _parse(raw)
            except _Drop as d:
                drop(d.reason, h, raw=raw)
                continue
            try:
                if isinstance(p, ProposedSourced):
                    sourced.append(_check_sourced(p, h, run, inputs, cfg))
                else:
                    _clean(p, cfg)
                    attribute_class(p.attribute_key, cfg)
                    others.append((h, p))
            except _Drop as d:
                drop(d.reason, h, p, hostile_url=d.hostile_url)
            except GradingError as error:
                drop(error.reason_code, h, p)

        graded: dict[str, GradedClaim] = {}
        by_local: dict[str, GradedClaim] = {}
        by_attr: dict[str, list[_Sourced]] = {}
        for s in sourced:
            by_attr.setdefault(s.proposal.attribute_key, []).append(s)
        out_leads: dict[str, LeadVerification] = {}
        pending: list[_Pending] = []
        for key in sorted(by_attr):
            group = by_attr[key]
            cls = attribute_class(key, cfg)
            norm_kind = normaliser_for(key, cfg)
            ev_all = [e for s in group for e in s.engine]
            attr: AttributeGrade = grade_attribute(
                ev_all, cls, now, cfg, normaliser=norm_kind, blocked_reason=blocked.get(key)
            )
            for s in sorted(group, key=lambda x: x.claim_hash):
                p = s.proposal
                conflict_values = (
                    {v.value: v for v in attr.values} if attr.status == "conflicting" else {}
                )
                conflicting = s.norm_value in conflict_values
                score = conflict_values.get(s.norm_value) or score_value(
                    s.norm_value, s.engine, cls, now, cfg
                )
                flags = (
                    set(s.flags)
                    | set(score.flags)
                    | set(attr.flags if (conflicting or attr.status != "conflicting") else ())
                )
                prim = read_in_primary_this_run(
                    s.verified, inputs.fetches, run, conflicting=conflicting
                )
                status: AttributeStatus = (
                    "conflicting"
                    if conflicting
                    else ("blocked" if attr.status == "blocked" else "graded")
                )
                inputs_ = ProductInputs(
                    status=status,
                    engine_grade=score.engine_grade if status != "blocked" else "BLOCKED",
                    read_in_primary=prim.value,
                    internal_only=key in internal_only_keys,
                    internal_authoritative=cfg.precedence.internal_authoritative_default,
                    secondary_only=s.secondary_only,
                    unread_match=p.match_origin is not None,
                    aggregator_only=s.aggregator_only and bool(s.evidence),
                )
                grade, rule = product_grade(inputs_, cfg)
                if not prim.value and any(e.origin == "full_page_read" for e in s.evidence):
                    flags.add("primary_read_refused:" + ",".join(prim.reasons))
                lead_rec = None
                if grade == "lead":
                    lk = f"{key}={s.norm_value}"
                    state = lead_states.get(lk) or new_lead(lk)
                    out_leads[lk] = state
                    lead_rec = LeadRecord(
                        status=evaluate_lead(state, cfg),
                        reason=state.reason,
                        accepted_queries=len(state.accepted),
                        tool_categories=tuple(sorted(state.categories)),
                    )
                question = p.confirming_question
                try:
                    claim = GradedClaim(
                        schema_version=SCHEMA_VERSION,
                        config_hash=cfg.content_hash,
                        claim_hash=s.claim_hash,
                        claim_kind="sourced",
                        attribute_key=key,
                        value=sanitise(p.value, cfg.spans.max_value_chars),
                        normalised_value=s.norm_value,
                        product_grade=grade,
                        engine_grade=score.engine_grade if status != "blocked" else "BLOCKED",
                        confidence=float(score.confidence),
                        read_in_primary_this_run=prim.value,
                        may_state_to_prospect=statement_policy(grade, cfg),
                        precedence_rule=rule,
                        evidence=tuple(s.evidence),
                        confirming_question=question,
                        reasoning=_reasoning(score, prim.value, p.reasoning)
                        if grade == "probable"
                        else p.reasoning,
                        flags=tuple(flags),
                        lead=lead_rec,
                    )
                    pending.append(_Pending(claim, s, p, score, prim.value, rule))
                except ValidationError as error:
                    drop(_drop_reason(error), s.claim_hash, p)
                    continue

        # the judge's verdicts are asked side by side (each is its own short call, seconds long)
        # and applied in the order the claims were built, so the result is the one-at-a-time loop's
        verdicts = await fan_out_all(
            pending,
            lambda item: _judge_all(item.claim, judge, judge_profile_ok, cfg),
            limit=JUDGE_CONCURRENCY,
        )
        for item, judged in zip(pending, verdicts, strict=True):
            claim, s, p, score, prim_value, rule = (
                item.claim,
                item.sourced,
                item.proposal,
                item.score,
                item.read_in_primary,
                item.rule,
            )
            try:
                if not judged.keep:
                    drop(judged.drop_reason or "unsupported_by_judge", s.claim_hash, p)
                    continue
                if (judged.engine_grade, judged.product_grade, judged.flags) != (
                    claim.engine_grade,
                    claim.product_grade,
                    claim.flags,
                ):
                    downgraded = judged.product_grade != claim.product_grade
                    claim = claim.model_copy(
                        update={
                            "engine_grade": judged.engine_grade,
                            "product_grade": judged.product_grade,
                            "read_in_primary_this_run": claim.read_in_primary_this_run,
                            "may_state_to_prospect": statement_policy(judged.product_grade, cfg),
                            "flags": tuple(sorted(set(judged.flags))),
                            "reasoning": _reasoning(score, prim_value, p.reasoning)
                            if downgraded
                            else claim.reasoning,
                            "precedence_rule": f"{rule}+judge_cap"
                            if downgraded or "unjudged" in judged.flags
                            else rule,
                        }
                    )
                    claim = GradedClaim.model_validate(claim.model_dump())  # re-run every rule
            except ValidationError as error:
                drop(_drop_reason(error), s.claim_hash, p)
                continue
            graded[s.claim_hash] = claim
            by_local[p.local_id] = claim

        for h, p in sorted(others, key=lambda x: x[0]):
            try:
                if isinstance(p, ProposedSynthesis):
                    basis = [by_local.get(b) for b in p.basis_claim_ids]
                    if any(b is None for b in basis):
                        raise _Drop("synthesis_basis_unresolved")
                    inherited = tuple(
                        e.model_copy(update={"evidence_kind": "inherited"})
                        for b in basis
                        if b
                        for e in b.evidence
                    )
                    grade, rule = product_grade(
                        ProductInputs("graded", None, False, synthesis=True), cfg
                    )
                    evidence = inherited
                else:
                    proofs = [inputs.fetches.get(c) for c in p.tracker_proof_call_refs]
                    if any(f is None or not f.success for f in proofs):
                        raise _Drop("absence_proof_invalid")
                    evidence = tuple(
                        GradedEvidence(
                            evidence_kind="search_proof",
                            url=None,
                            supporting_sentence=f"No supporting source found by {f.operation}"
                            f" ({f.tool_category})"
                            + (f": {'; '.join(f.queries)}" if f.queries else ""),
                            span_sha256=None,
                            source_ref=None,
                            call_ref=f.call_ref,
                            origin=f.origin,
                            tier=None,
                            lineage_group=None,
                            is_aggregator=False,
                            date_kind=None,
                            evidence_date=None,
                            provider_code=f.provider_code,
                        )
                        for f in proofs
                        if f is not None
                    )
                    grade, rule = product_grade(ProductInputs("no_evidence", None, False), cfg)
                claim = GradedClaim(
                    schema_version=SCHEMA_VERSION,
                    config_hash=cfg.content_hash,
                    claim_hash=h,
                    claim_kind=p.claim_kind,
                    attribute_key=p.attribute_key,
                    value=sanitise(p.value, cfg.spans.max_value_chars),
                    normalised_value=normalise_value(p.value, normaliser_for(p.attribute_key, cfg)),
                    product_grade=grade,
                    engine_grade=None,
                    confidence=None,
                    read_in_primary_this_run=False,
                    may_state_to_prospect=statement_policy(grade, cfg),
                    precedence_rule=rule,
                    evidence=evidence,
                    confirming_question=p.confirming_question,
                    reasoning=getattr(p, "reasoning", None),
                    flags=("absence",) if isinstance(p, ProposedAbsence) else ("synthesis",),
                    lead=None,
                )
                graded[h] = claim
            except _Drop as d:
                drop(d.reason, h, p)
            except ValidationError as error:
                drop(_drop_reason(error), h, p)

    final = tuple(graded[k] for k in sorted(graded))
    drops = tuple(sorted(dropped, key=lambda d: (d.claim_hash, d.reason)))
    result = BatchResult(
        graded=final,
        dropped=drops,
        drop_counts=dict(sorted(Counter(d.reason for d in drops).items())),
        grade_counts=dict(sorted(Counter(g.product_grade for g in final).items())),
        hostile_urls=tuple(sorted(hostile)),
        leads=dict(sorted(out_leads.items())),
    )
    obs.record_batch(result.grade_counts, result.drop_counts)
    return result


__all__ = [
    "BatchResult",
    "Dropped",
    "GradingInputs",
    "SchemaViolation",
    "grade_claims",
    "load_inputs",
]
