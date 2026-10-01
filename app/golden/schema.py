"""Golden account schema (roadmap s6 golden accounts).

A golden account is a reviewed regression baseline: the account, the sources read, the
known-correct facts with grade, URL and supporting sentence, the expected coverage status of
each checklist row, and the expected people counts. The JSON schema in
tests/evals/golden/golden_account.schema.json is generated from this model.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.rules.schemas import CoverageStatus, GradeName


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Account(_Strict):
    name: str = Field(min_length=1)
    domain: str = Field(pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    industry: str
    primary_lane: str


class Source(_Strict):
    source_id: str = Field(pattern=r"^S\d{3,}$")
    url: HttpUrl
    published: date | None = None
    primary: bool
    row_ids: list[str] = Field(min_length=1, description="Coverage rows this source serves")
    text: str = Field(min_length=1, description="Text of the source as read during the run")


class KnownFact(_Strict):
    statement: str = Field(min_length=1)
    grade: GradeName
    source_ids: list[str] = Field(min_length=1)
    url: HttpUrl
    supporting_sentence: str = Field(min_length=1)
    reasoning: str | None = None
    confirming_question: str | None = None


class CoverageExpectation(_Strict):
    row_id: str
    status: CoverageStatus


class PeopleCounts(_Strict):
    total: int = Field(ge=0)
    priority_a: int = Field(ge=0)
    by_lane: dict[str, int] = Field(default_factory=dict)
    excluded: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> PeopleCounts:
        if self.priority_a > self.total:
            raise ValueError("priority_a cannot exceed total")
        if self.by_lane and sum(self.by_lane.values()) != self.total:
            raise ValueError("by_lane counts must sum to total")
        return self


class Review(_Strict):
    reviewed_by: str | None = None
    reviewed_on: date | None = None
    source_run: str = Field(description="Which September run this baseline came from")


class GoldenAccount(_Strict):
    schema_version: str = "1.0.0"
    golden_id: str = Field(pattern=r"^[a-z0-9_]+$")
    synthetic: bool = Field(
        description="True for placeholder data; such accounts are never valid parity evidence"
    )
    account: Account
    review: Review
    sources: list[Source] = Field(min_length=1)
    known_facts: list[KnownFact] = Field(min_length=1)
    expected_coverage: list[CoverageExpectation] = Field(min_length=1)
    expected_people: PeopleCounts

    @model_validator(mode="after")
    def _facts_trace_to_sources(self) -> GoldenAccount:
        by_id = {s.source_id: s for s in self.sources}
        if len(by_id) != len(self.sources):
            raise ValueError("source ids must be unique")
        for fact in self.known_facts:
            cited = [by_id.get(sid) for sid in fact.source_ids]
            if any(s is None for s in cited):
                raise ValueError(f"fact {fact.statement!r} cites an unknown source")
            if not any(str(s.url) == str(fact.url) for s in cited if s):
                raise ValueError(f"fact {fact.statement!r}: url is not one of its sources")
            if not any(fact.supporting_sentence in s.text for s in cited if s):
                raise ValueError(
                    f"fact {fact.statement!r}: supporting sentence not found in cited source"
                )
            if fact.grade is GradeName.CONFIRMED and not any(s.primary for s in cited if s):
                raise ValueError(f"fact {fact.statement!r}: Confirmed needs a primary source")
            if fact.grade is GradeName.HYPOTHESIS and not fact.confirming_question:
                raise ValueError(f"fact {fact.statement!r}: Hypothesis needs a question")
            if fact.grade is GradeName.PROBABLE and not fact.reasoning:
                raise ValueError(f"fact {fact.statement!r}: Probable needs stated reasoning")
        rows = [c.row_id for c in self.expected_coverage]
        if len(rows) != len(set(rows)):
            raise ValueError("each coverage row may appear once")
        if not self.synthetic and (
            self.review.reviewed_by is None or self.review.reviewed_on is None
        ):
            raise ValueError("a real golden account must name its analyst reviewer and date")
        return self
