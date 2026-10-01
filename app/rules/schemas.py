"""Pydantic schemas for the versioned rule config in config/*.yaml.

Each schema validates structure and the cross-field rules the roadmap and PRD fix, so a
config edit that breaks the trust model fails at load time rather than mid-run.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Versioned(_Strict):
    version: str

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER.match(value):
            raise ValueError(f"version must be semantic (x.y.z), got {value!r}")
        return value


# --------------------------------------------------------------------------- checklist


class Priority(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    OPTIONAL = "Optional"


class CoverageStatus(StrEnum):
    DONE = "Done"
    PARTIAL = "Partial"
    NOT_DONE = "Not done"
    BLOCKED = "Blocked"
    NONE = "None"


# PRD Appendix A: 5 rows in pass 1, 28 in pass 2.
EXPECTED_ROWS_BY_PASS = {1: 5, 2: 28}
ROW_ID = re.compile(r"^P(?P<pass>[12])\.(?P<n>\d{2})$")


class ShelfLife(_Strict):
    days: int | None = Field(default=None, gt=0)
    basis: str = Field(min_length=1)


class CoverageRow(_Strict):
    id: str
    pass_: int = Field(alias="pass")
    row: str = Field(min_length=1)
    priority: Priority
    shelf_life: str

    @model_validator(mode="after")
    def _id_matches_pass(self) -> CoverageRow:
        match = ROW_ID.match(self.id)
        if not match:
            raise ValueError(f"row id {self.id!r} must look like P1.01")
        if int(match["pass"]) != self.pass_:
            raise ValueError(f"row {self.id} declares pass {self.pass_}")
        return self


class Checklist(_Versioned):
    statuses: list[CoverageStatus]
    proof_fields: list[str]
    shelf_lives: dict[str, ShelfLife]
    rows: list[CoverageRow]

    @model_validator(mode="after")
    def _rows_are_complete(self) -> Checklist:
        if set(self.statuses) != set(CoverageStatus):
            raise ValueError("statuses must be exactly Done, Partial, Not done, Blocked, None")
        if set(self.proof_fields) != {"queries", "tool", "result_count"}:
            raise ValueError("proof_fields must be queries, tool, result_count")
        ids = [r.id for r in self.rows]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate coverage row ids: {dupes}")
        for pass_no, expected in EXPECTED_ROWS_BY_PASS.items():
            got = sum(1 for r in self.rows if r.pass_ == pass_no)
            if got != expected:
                raise ValueError(f"pass {pass_no} must have {expected} rows, found {got}")
        unknown = sorted({r.shelf_life for r in self.rows} - set(self.shelf_lives))
        if unknown:
            raise ValueError(f"rows reference undefined shelf lives: {unknown}")
        return self

    def row(self, row_id: str) -> CoverageRow:
        for r in self.rows:
            if r.id == row_id:
                return r
        raise KeyError(row_id)

    @property
    def high_priority_ids(self) -> frozenset[str]:
        return frozenset(r.id for r in self.rows if r.priority is Priority.HIGH)

    def rows_without_shelf_life(self) -> list[str]:
        return [r.id for r in self.rows if self.shelf_lives[r.shelf_life].days is None]


# --------------------------------------------------------------------------- grading


class GradeName(StrEnum):
    CONFIRMED = "Confirmed"
    PROBABLE = "Probable"
    HYPOTHESIS = "Hypothesis"
    LEAD = "Lead"


class Grade(_Strict):
    name: GradeName
    meaning: str
    use: str
    may_state_to_prospect: bool
    requires_primary_source_this_run: bool
    requires_reasoning: bool
    requires_confirming_question: bool


class LeadVerification(_Strict):
    min_distinct_queries: int = Field(ge=1)
    min_distinct_tools: int = Field(ge=1)


class GradingRules(_Strict):
    absence_of_evidence_grade: GradeName
    keyword_match_grade: GradeName
    lead_verification: LeadVerification
    aggregator_allowed_grades: list[GradeName]


class Grading(_Versioned):
    required_fact_fields: list[str]
    grades: list[Grade]
    rules: GradingRules

    @model_validator(mode="after")
    def _trust_model_is_intact(self) -> Grading:
        names = [g.name for g in self.grades]
        if sorted(names) != sorted(GradeName):
            raise ValueError("grades must be exactly Confirmed, Probable, Hypothesis, Lead")
        if not {"grade", "url", "supporting_sentence"} <= set(self.required_fact_fields):
            raise ValueError("every fact must require grade, url and supporting_sentence")
        stateable = [g.name for g in self.grades if g.may_state_to_prospect]
        if stateable != [GradeName.CONFIRMED]:
            raise ValueError("only Confirmed may be stated to a prospect")
        if self.rules.absence_of_evidence_grade is not GradeName.HYPOTHESIS:
            raise ValueError("absence of evidence must be graded Hypothesis")
        if self.rules.keyword_match_grade is not GradeName.LEAD:
            raise ValueError("a keyword match must be graded Lead")
        lv = self.rules.lead_verification
        if lv.min_distinct_queries < 3 or lv.min_distinct_tools < 2:
            raise ValueError("Lead verification needs at least 3 queries across 2 tools")
        if GradeName.CONFIRMED in self.rules.aggregator_allowed_grades:
            raise ValueError("aggregator sources cannot support a Confirmed grade")
        return self

    def grade(self, name: GradeName | str) -> Grade:
        for g in self.grades:
            if g.name == name:
                return g
        raise KeyError(name)


# --------------------------------------------------------------------------- routing


class Broker(_Strict):
    display_name: str
    paid: bool


class RetryPolicy(_Strict):
    max_attempts: int = Field(ge=1, le=10)
    backoff_initial_seconds: float = Field(gt=0)
    backoff_multiplier: float = Field(ge=1)


class ConnectorPolicy(_Strict):
    pull_once_per_run: Literal[True]
    cache_key: Literal["request"]
    raw_storage_naming: Literal["neutral"]
    record_cost_per_call: Literal[True]
    retry: RetryPolicy


class Endpoint(_Strict):
    broker: str
    operation: str


class Route(_Strict):
    need: str
    description: str
    first_choice: Endpoint
    fallback: Endpoint | None
    cost_note: str
    avoid_operations: list[str] = Field(default_factory=list)


class Routing(_Versioned):
    brokers: dict[str, Broker]
    connector_policy: ConnectorPolicy
    routes: list[Route]

    @model_validator(mode="after")
    def _routes_reference_known_brokers(self) -> Routing:
        needs = [r.need for r in self.routes]
        if len(needs) != len(set(needs)):
            raise ValueError("each need may be routed once")
        for r in self.routes:
            for ep in (r.first_choice, r.fallback):
                if ep is not None and ep.broker not in self.brokers:
                    raise ValueError(f"route {r.need!r} uses unknown broker {ep.broker!r}")
            if r.fallback is not None and r.fallback == r.first_choice:
                raise ValueError(f"route {r.need!r} falls back to itself")
        return self

    def route(self, need: str) -> Route:
        for r in self.routes:
            if r.need == need:
                return r
        raise KeyError(need)

    def client_banned_terms(self) -> list[str]:
        """Names that must never appear in client-facing output (roadmap s3 principle 6)."""
        terms = {b.display_name.lower() for k, b in self.brokers.items() if k != "builtin"}
        terms |= {k.lower() for k in self.brokers if k != "builtin"}
        return sorted(terms)


# --------------------------------------------------------------------------- output spec


class Audience(StrEnum):
    CLIENT = "client"
    OPERATOR_ONLY = "operator_only"


class Deliverable(_Strict):
    id: str
    filename: str
    format: Literal["docx", "xlsx", "zip", "pdf", "csv"]
    audience: Audience
    contents: list[str] = Field(min_length=1)
    includes: list[str] = Field(default_factory=list)

    @field_validator("filename")
    @classmethod
    def _has_account_placeholder(cls, value: str) -> str:
        if "{account}" not in value:
            raise ValueError("deliverable filename must contain {account}")
        return value


class GateCheck(_Strict):
    id: str
    description: str


class DocumentRules(_Strict):
    page_size: str
    banned_characters: list[str]


class OutputSpec(_Versioned):
    deliverables: list[Deliverable]
    on_request_only: list[str]
    quality_gate: list[GateCheck] = Field(min_length=1)
    document_rules: DocumentRules

    @model_validator(mode="after")
    def _operator_only_never_shipped(self) -> OutputSpec:
        by_id = {d.id: d for d in self.deliverables}
        if len(by_id) != len(self.deliverables):
            raise ValueError("deliverable ids must be unique")
        for d in self.deliverables:
            for inc in d.includes:
                if inc not in by_id:
                    raise ValueError(f"{d.id} includes unknown deliverable {inc!r}")
                if d.audience is Audience.CLIENT and by_id[inc].audience is not Audience.CLIENT:
                    raise ValueError(f"client deliverable {d.id} cannot include {inc}")
        return self


# --------------------------------------------------------------------------- models


class ModelProfile(_Strict):
    provider: Literal["anthropic", "fake"]
    model: str
    input_usd_per_mtok: float | None = Field(default=None, ge=0)
    output_usd_per_mtok: float | None = Field(default=None, ge=0)


class Arm(_Strict):
    description: str
    routes: dict[str, str]


class Models(_Versioned):
    tasks: list[str] = Field(min_length=1)
    profiles: dict[str, ModelProfile]
    arms: dict[str, Arm]
    active_arm: str
    decision_status: Literal["Proposed", "Decided"]

    @model_validator(mode="after")
    def _every_task_routed(self) -> Models:
        for name, arm in self.arms.items():
            missing = sorted(set(self.tasks) - set(arm.routes))
            extra = sorted(set(arm.routes) - set(self.tasks))
            if missing or extra:
                raise ValueError(f"arm {name!r}: missing tasks {missing}, unknown tasks {extra}")
            bad = sorted({p for p in arm.routes.values() if p not in self.profiles})
            if bad:
                raise ValueError(f"arm {name!r} uses unknown profiles {bad}")
        if self.active_arm not in self.arms:
            raise ValueError(f"active_arm {self.active_arm!r} is not a defined arm")
        return self


# --------------------------------------------------------------------------- people scope


class PeopleScope(_Versioned):
    levels_in_scope: list[str] = Field(min_length=1)
    excluded_title_terms: list[str]
    merge_keys: list[Literal["profile_url", "name"]]

    @model_validator(mode="after")
    def _merge_order(self) -> PeopleScope:
        if self.merge_keys != ["profile_url", "name"]:
            raise ValueError("people merge must be by profile_url first, then name")
        return self


# --------------------------------------------------------------------------- lanes

LANE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class Lane(_Strict):
    id: str
    label: str = Field(min_length=1)
    note: str = ""

    @field_validator("id")
    @classmethod
    def _id_is_a_slug(cls, value: str) -> str:
        if not LANE_ID.match(value):
            raise ValueError(f"lane id {value!r} must be lowercase letters, digits and _")
        return value


class Lanes(_Versioned):
    lanes: list[Lane] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_and_has_other(self) -> Lanes:
        ids = [lane.id for lane in self.lanes]
        if len(ids) != len(set(ids)):
            raise ValueError("lane ids must be unique")
        if "other" not in ids:
            raise ValueError("lanes must include 'other' for activity outside the default set")
        return self

    @property
    def ids(self) -> list[str]:
        return [lane.id for lane in self.lanes]
