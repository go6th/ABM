"""Schemas: what a model may propose, and what the pipeline builds (G1, G2, G6, G8).

``ProposedClaim`` (model output) has **no** grade, confidence, tier, lineage or read flag;
``extra='forbid'`` rejects any attempt to send one (G1). ``GradedClaim`` is built only by
``pipeline.grade_claims``; its validators enforce G2, G6 and G8 whoever constructs it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from account_intelligence.grading.config import EngineGrade, Origin, ProductGrade, Tier

SCHEMA_VERSION = 1

AttributeKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", max_length=120)]
LocalId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
Text = Annotated[str, Field(min_length=1, max_length=1000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProposedEvidence(_Strict):
    url: Annotated[str, Field(min_length=8, max_length=2048)]
    supporting_sentence: Annotated[str, Field(min_length=1, max_length=2000)]


class ProposedSourced(_Strict):
    claim_kind: Literal["sourced"]
    local_id: LocalId
    attribute_key: AttributeKey
    value: Text
    evidence: tuple[ProposedEvidence, ...] = Field(min_length=1, max_length=10)
    reasoning: Text | None = None
    confirming_question: Text | None = None


class ProposedSynthesis(_Strict):
    claim_kind: Literal["synthesis"]
    local_id: LocalId
    attribute_key: AttributeKey
    value: Text
    basis_claim_ids: tuple[LocalId, ...] = Field(min_length=1, max_length=10)
    confirming_question: Text
    reasoning: Text


class ProposedAbsence(_Strict):
    claim_kind: Literal["absence"]
    local_id: LocalId
    attribute_key: AttributeKey
    value: Text  # the hypothesis the absence suggests, stated as such
    tracker_proof_call_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    confirming_question: Text


ProposedClaim = Annotated[
    ProposedSourced | ProposedSynthesis | ProposedAbsence, Field(discriminator="claim_kind")
]
PROPOSED_ADAPTER: TypeAdapter[ProposedSourced | ProposedSynthesis | ProposedAbsence] = TypeAdapter(
    ProposedClaim
)

EvidenceKind = Literal["source", "inherited", "search_proof"]
DateKind = Literal["observed", "published", "unknown"]


class GradedEvidence(_Strict):
    evidence_kind: EvidenceKind
    url: str | None
    supporting_sentence: Annotated[str, Field(min_length=1, max_length=2000)]
    span_sha256: str | None
    source_ref: str | None
    call_ref: str | None
    origin: Origin | None
    tier: Tier | None
    lineage_group: str | None
    is_aggregator: bool
    date_kind: DateKind | None
    evidence_date: datetime | None
    provider_code: str | None  # internal only; never rendered to a client

    @model_validator(mode="after")
    def _traceable(self) -> GradedEvidence:
        if self.evidence_kind in ("source", "inherited"):
            missing = [n for n in ("url", "span_sha256", "tier", "origin", "evidence_date") if getattr(self, n) is None]
            if missing:
                raise ValueError(f"{self.evidence_kind} evidence needs {missing}")
        if self.evidence_kind == "search_proof" and self.call_ref is None:
            raise ValueError("search_proof evidence needs the call it proves")
        return self


class LeadRecord(_Strict):
    status: Literal["pending", "complete", "upgraded", "incomplete"]
    reason: str | None
    accepted_queries: int
    tool_categories: tuple[str, ...]


class GradedClaim(_Strict):
    schema_version: int
    config_hash: str
    claim_hash: str
    claim_kind: Literal["sourced", "synthesis", "absence"]
    attribute_key: AttributeKey
    value: Text
    normalised_value: str
    product_grade: ProductGrade
    engine_grade: EngineGrade | None
    confidence: Annotated[float, Field(ge=0, le=1)] | None
    read_in_primary_this_run: bool
    may_state_to_prospect: bool
    precedence_rule: str
    evidence: tuple[GradedEvidence, ...] = Field(min_length=1)
    confirming_question: Text | None
    reasoning: Text | None
    flags: tuple[str, ...]
    lead: LeadRecord | None

    @field_validator("flags")
    @classmethod
    def _sorted(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(v)))

    @model_validator(mode="after")
    def _rules(self) -> GradedClaim:
        kinds = {e.evidence_kind for e in self.evidence}
        expected = {"sourced": {"source"}, "synthesis": {"inherited"}, "absence": {"search_proof"}}[self.claim_kind]
        if kinds != expected:
            raise ValueError(f"{self.claim_kind} claim needs {sorted(expected)} evidence, got {sorted(kinds)}")
        if self.product_grade == "confirmed" and not self.read_in_primary_this_run:
            raise ValueError("confirmed requires a code-verified primary read this run (G5)")
        if self.may_state_to_prospect != (self.product_grade == "confirmed"):
            raise ValueError("only confirmed may be stated to a prospect (G6)")
        if self.product_grade == "hypothesis" and not self.confirming_question:
            raise ValueError("a hypothesis needs a confirming question (G8)")
        if self.product_grade == "probable" and not self.reasoning:
            raise ValueError("a probable needs stated reasoning (G8)")
        if self.claim_kind != "sourced" and self.product_grade not in ("hypothesis",):
            raise ValueError("synthesis and absence claims are hypotheses (G7)")
        if self.product_grade == "lead" and self.lead is None:
            raise ValueError("a lead needs its verification record (G9)")
        if "conflicting" in self.flags and not self.confirming_question:
            raise ValueError("a conflicting value needs a question (G13)")
        return self


def export_json_schema() -> str:
    """JSON Schema for both models (docs/grading/schema.json)."""
    doc = {
        "$comment": "Generated by account_intelligence.grading.docs; do not edit.",
        "schema_version": SCHEMA_VERSION,
        "ProposedClaim": PROPOSED_ADAPTER.json_schema(),
        "GradedClaim": GradedClaim.model_json_schema(),
    }
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"
