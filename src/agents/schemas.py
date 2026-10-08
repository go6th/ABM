"""Typed inputs and outputs of every agent (registry contract, D-95).

All models forbid extra fields, so an output carrying a ``grade``, a ``tool_call``, a
``tenant_id`` or any field outside its schema fails validation and is rejected whole (the lying
model cases). No output schema has a grade, a URL the agent writes, or a free-text field that
could carry an interpretation of a pull: the Pull agent's output is ``ManifestRow`` (G5).

GUARANTEE(test_output_schemas_have_no_grade_or_tool_fields)
GUARANTEE(test_g5_pull_layer_has_no_model_and_no_interpretive_output)
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agents.manifest import ManifestRow, QueryRecord

Visibility = Literal["public", "private"]
Origin = Literal["read", "keyword_match", "date_range"]
SenseKind = Literal[
    "entity_boundary", "adjacent_organisation", "hidden_title", "contradictory_fact"
]
_KEY = r"^[a-z0-9_]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# Step 1: Setup assistant -------------------------------------------------------------------------


class VisitedPage(_Strict):
    """A page the pull layer fetched this run (the only careers URLs setup may choose from)."""

    page_id: str = Field(pattern=r"^[A-Za-z0-9_\-:]+$")
    url: str = Field(pattern=r"^https?://")
    title: str = Field(max_length=300)


class SetupInput(_Strict):
    """The run request as the API validated it plus tenant settings. Never a tenant id."""

    account_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253, pattern=r"^([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
    lanes: tuple[str, ...] = Field(min_length=1)
    pursuing_client: str = Field(min_length=1, max_length=200)
    depth: Literal["standard", "deep"] = "standard"
    cap_request_micros: int = Field(ge=0)
    visited_pages: tuple[VisitedPage, ...] = ()


class SetupProposal(_Strict):
    """The model's only choice: which visited page is the careers page (or none)."""

    careers_page_id: str | None = None


# Step 2 (pull) -----------------------------------------------------------------------------------


class PullInput(_Strict):
    """One row to pull: code-built queries and the route need. No model involved."""

    row_key: str = Field(pattern=_KEY)
    need: str = Field(pattern=_KEY)
    queries: tuple[str, ...] = Field(min_length=1)


PullRowResult = ManifestRow


# Steps 2 to 4 (interpretation, Phase 2) ----------------------------------------------------------


class PayloadView(_Strict):
    """A stored payload as the extractor sees it (quarantined text; read-only)."""

    payload_ref: str = Field(min_length=1)
    row_key: str = Field(pattern=_KEY)
    url: str
    text: str
    visibility: Visibility = "public"


class ExtractionInput(_Strict):
    """What one extraction call sees: one step and source group (and lane) of stored payloads."""

    step: Literal["pass1", "pass2", "people"]
    group: str = Field(pattern=_KEY)
    lane: str | None = None
    payloads: tuple[PayloadView, ...]


class EvidenceRef(_Strict):
    """A verbatim quote from a payload this call was given (G4)."""

    payload_ref: str = Field(min_length=1)
    quote: str = Field(min_length=8, max_length=600)


class FactCandidate(_Strict):
    """A proposed fact. No grade: the grading engine assigns it (G2, G4)."""

    row_key: str = Field(pattern=_KEY)
    attribute: str = Field(pattern=r"^[a-z0-9_.]+$", max_length=80)
    value: str = Field(min_length=1, max_length=300)
    lane: str | None = None
    origin: Origin
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=5)


class LeadDropRequest(_Strict):
    """A request to drop an open lead, with the queries tried (G3)."""

    lead_key: str = Field(pattern=_KEY)
    attempts: tuple[QueryRecord, ...]


class ExtractionOutput(_Strict):
    """Candidates and lead-drop requests from one extraction call."""

    candidates: tuple[FactCandidate, ...] = Field(default=(), max_length=200)
    lead_drops: tuple[LeadDropRequest, ...] = ()


class PersonCandidate(_Strict):
    """A proposed senior person. Title and tenure must be quoted from a payload (G4, people)."""

    name: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=200)
    lane: str | None = None
    priority: Literal["A", "B", "C"]
    tenure_text: str | None = Field(default=None, max_length=80)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=3)


class PeopleOutput(_Strict):
    """The people agent's proposals."""

    people: tuple[PersonCandidate, ...] = Field(default=(), max_length=300)


# Step 5a: sense check ----------------------------------------------------------------------------


class StoredFact(_Strict):
    """A graded fact as later agents see it (grade from the engine, read-only)."""

    fact_id: str = Field(min_length=1)
    row_key: str = Field(pattern=_KEY)
    attribute: str
    value: str
    grade: str
    visibility: Visibility = "public"


class SenseCheckInput(_Strict):
    """The stored facts the sense check examines, bound to the manifest they came from (G6)."""

    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_name: str
    domain: str
    facts: tuple[StoredFact, ...]


class SenseFinding(_Strict):
    """A typed sense-check finding over stored facts."""

    kind: SenseKind
    fact_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    note: str = Field(min_length=1, max_length=500)


class PullProposal(_Strict):
    """A targeted pull the sense check recommends; priced by the credit gate in code (D-96)."""

    row_key: str = Field(pattern=_KEY)
    query: str = Field(min_length=3, max_length=300)
    reason: SenseKind


class SenseCheckOutput(_Strict):
    """Findings and recommended pulls. Nothing here changes a fact or spends."""

    findings: tuple[SenseFinding, ...] = Field(default=(), max_length=100)
    proposals: tuple[PullProposal, ...] = Field(default=(), max_length=20)


# Step 5: distill ---------------------------------------------------------------------------------


class DistillInput(_Strict):
    """Graded facts for one audience (public facts only for client output, G10)."""

    account_name: str
    audience: Literal["client", "internal"]
    facts: tuple[StoredFact, ...]


class Claim(_Strict):
    """A distilled statement tied to the facts it rests on (no claim without a fact id)."""

    text: str = Field(min_length=1, max_length=600)
    fact_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class DistillOutput(_Strict):
    """Hypotheses, first-call questions and narrative."""

    hypotheses: tuple[Claim, ...] = Field(default=(), max_length=30)
    questions: tuple[Claim, ...] = Field(default=(), max_length=30)
    narrative: tuple[Claim, ...] = Field(default=(), max_length=30)


SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        SetupInput,
        SetupProposal,
        PullInput,
        ManifestRow,
        ExtractionInput,
        ExtractionOutput,
        PeopleOutput,
        SenseCheckInput,
        SenseCheckOutput,
        DistillInput,
        DistillOutput,
    )
}
SCHEMAS["PullRowResult"] = ManifestRow


class RunRequest(_Strict):
    """The typed start-run request (API and form). The mode is a field, never parsed text."""

    account_name: str = Field(min_length=1, max_length=200)
    domain: str
    mode: Literal["full", "pull", "build"] | None = None
    parent_run_id: UUID | None = None
    acknowledged_stale_rows: tuple[str, ...] = ()
    config_version: str | None = None
