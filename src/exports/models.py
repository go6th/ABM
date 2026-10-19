"""Render models for the five files, the workspace and the manifest (frozen, ``extra='forbid'``).

Readers build these from one snapshot; renderers consume nothing else. Every rendered table row
carries a visible ``ref`` (the row key the PDF row-presence check finds), an internal ``row_id``
(recorded in the manifest) and, where the row is a fact, posting, article or person, its URL and
stable source code. A model without provenance cannot be constructed (B4).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

_CODE = re.compile(r"^S-\d{4,}$")
_URL = re.compile(r"^https?://\S+$")


def _code(value: str) -> str:
    if not _CODE.match(value):
        raise ValueError("a source code looks like S-0001")
    return value


def _url(value: str) -> str:
    if not _URL.match(value):
        raise ValueError("a rendered URL is absolute http(s) without spaces")
    return value


SourceCode = Annotated[str, AfterValidator(_code)]
Url = Annotated[str, AfterValidator(_url)]
Ref = Annotated[str, Field(pattern=r"^[A-Z]{1,3}-\d{4,}$")]
Text = Annotated[str, Field(min_length=1)]
FileKind = Literal["summary", "evidence", "people", "jobs", "tech"]
BuildKind = Literal["summary", "evidence", "people", "jobs", "tech", "workspace"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CountedNumber(_Frozen):
    """A rendered number tied to the count computed in code and the base it names (B6)."""

    key: str
    value: int = Field(ge=0)
    base: Text
    sentence: Text


# ---- Account Summary -----------------------------------------------------------------------


class AuthoredBlock(_Frozen):
    """One stored ``report_sections`` paragraph (written by distill; validated, never edited)."""

    row_id: str
    text: Text
    codes: tuple[SourceCode, ...]


class QuoteBlock(_Frozen):
    """Executive voice: the executive's own words, verbatim, with the speaker named."""

    row_id: str
    text: Text
    speaker: Text
    codes: tuple[SourceCode, ...] = Field(min_length=1)


class HypothesisBlock(_Frozen):
    """A hypothesis and the question that would confirm it (B7)."""

    row_id: str
    text: Text
    question: Text
    codes: tuple[SourceCode, ...]


class LaneRow(_Frozen):
    """One graded fact in a lane table; the statement policy is already applied (B7)."""

    ref: Ref
    row_id: str
    finding: Text
    grade_label: Text
    use: Text
    may_state: bool
    question: str | None
    codes: tuple[SourceCode, ...] = Field(min_length=1)


class LaneTable(_Frozen):
    """One lane's graded rows."""

    lane: Text
    rows: tuple[LaneRow, ...] = Field(min_length=1)


class KeyPersonRow(_Frozen):
    """A priority A or senior person in the Summary's key people table."""

    ref: Ref
    row_id: str
    name: Text
    title: str
    level: str
    lanes: str
    priority: str
    profile_url: Url
    source_code: SourceCode


class FindingBlock(_Frozen):
    """A Findings from LinkedIn item: the templated claim, the person's own verbatim sentence
    (or the withheld note), the post link and its code."""

    ref: Ref
    row_id: str
    person: Text
    claim: Text
    sentence: Text
    withheld: bool
    url: Url
    source_code: SourceCode


class TrackerRow(_Frozen):
    """One coverage row from the tracker's client view (never typed by the builder)."""

    ref: Ref
    row_id: str
    row: Text
    status: Text
    priority: Text
    reason: str | None
    tools: str
    results: str


class SummaryModel(_Frozen):
    """The Account Summary, in the PRD order."""

    account: Text
    as_of: date
    narrative: tuple[AuthoredBlock, ...] = Field(min_length=1)
    caveats: tuple[AuthoredBlock, ...]
    sensitive_sentence: Text
    lanes: tuple[LaneTable, ...]
    key_people: tuple[KeyPersonRow, ...]
    linkedin_findings: tuple[FindingBlock, ...]
    executive_voice: tuple[QuoteBlock, ...]
    hypotheses: tuple[HypothesisBlock, ...]
    absences: tuple[HypothesisBlock, ...]
    first_call_questions: tuple[AuthoredBlock, ...]
    decision_changes: tuple[AuthoredBlock, ...]
    tracker: tuple[TrackerRow, ...] = Field(min_length=1)
    tracker_tally: tuple[Text, ...] = Field(min_length=1)
    none_found: Text
    brand: Text | None = None


# ---- Sources and Evidence ------------------------------------------------------------------


class EvidenceUse(_Frozen):
    """One sentence relied on from a source, with the grade of the row it supports."""

    row_id: str
    grade_label: Text
    sentence: Text
    withheld: bool


class EvidenceSource(_Frozen):
    """One source: stable code, link, date, kind and every sentence relied on."""

    ref: Ref
    code: SourceCode
    url: Url
    published: date | None
    kind_label: Text
    uses: tuple[EvidenceUse, ...]


class JobRead(_Frozen):
    """A job description read in full."""

    ref: Ref
    row_id: str
    title: Text
    url: Url
    code: SourceCode


class NewsItem(_Frozen):
    """A news item and whether it was verified at the source article."""

    ref: Ref
    row_id: str
    sentence: Text
    url: Url
    code: SourceCode
    verified: bool


class EvidenceModel(_Frozen):
    """The Sources and Evidence document."""

    account: Text
    as_of: date
    sources: tuple[EvidenceSource, ...]
    withheld: int = Field(ge=0)
    job_descriptions: tuple[JobRead, ...]
    news: tuple[NewsItem, ...]
    none_found: Text
    brand: Text | None = None

    @model_validator(mode="after")
    def _unique_codes(self) -> EvidenceModel:
        codes = [s.code for s in self.sources]
        if len(codes) != len(set(codes)):
            raise ValueError("a source code appears twice in the Evidence file")
        if self.withheld != sum(1 for s in self.sources for u in s.uses if u.withheld):
            raise ValueError("the withheld count disagrees with the withheld sentences")
        return self


# ---- workbooks -----------------------------------------------------------------------------


class PeopleRow(_Frozen):
    """One deduplicated senior person."""

    ref: Ref
    row_id: str
    name: Text
    title: str
    level: str
    lanes: str
    priority: str
    profile_url: Url
    source_code: SourceCode


class SummaryLine(_Frozen):
    """One Summary sheet line: a status and reason with its count, never a name."""

    status: Text
    reason: str
    count: int = Field(ge=0)


class PeopleModel(_Frozen):
    """The People List workbook."""

    account: Text
    as_of: date
    rows: tuple[PeopleRow, ...]
    summary: tuple[SummaryLine, ...] = Field(min_length=1)
    sentences: tuple[Text, ...]
    in_scope_listed: int = Field(ge=0)

    @model_validator(mode="after")
    def _listed(self) -> PeopleModel:
        if self.in_scope_listed != len(self.rows):
            raise ValueError("the listed count is the number of rows")
        return self


class JobRow(_Frozen):
    """One posting."""

    ref: Ref
    row_id: str
    title: Text
    status: Text
    posting_class: Text
    platforms: str
    platform_sentence: str
    description: str
    url: Url
    posted_at: date | None
    first_seen: date | None
    source_code: SourceCode


class CountLine(_Frozen):
    """A count line on a Counts sheet; ``formula`` marks lines the workbook recomputes (B12)."""

    key: str
    label: Text
    value: int = Field(ge=0)
    base: Text
    formula: Literal["rows", "open", "closed", "none"] = "none"


class JobsModel(_Frozen):
    """The Job Postings workbook."""

    account: Text
    as_of: date
    rows: tuple[JobRow, ...]
    counts: tuple[CountLine, ...] = Field(min_length=1)
    sentences: tuple[Text, ...]


class TechRow(_Frozen):
    """One technology detection with its dates and the posting cross-check."""

    ref: Ref
    row_id: str
    technology: Text
    first_seen: str
    last_seen: str
    cross_check: Text
    grade: Text
    source_code: SourceCode


class TechModel(_Frozen):
    """The Tech Stack and Teams workbook."""

    account: Text
    as_of: date
    rows: tuple[TechRow, ...]
    counts: tuple[CountLine, ...] = Field(min_length=1)
    teams_note: Text


# ---- manifest and deliverables ------------------------------------------------------------


class FileEntry(_Frozen):
    """One built file in the manifest."""

    kind: BuildKind
    filename: Text
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: int = Field(ge=0)


class BuildManifest(_Frozen):
    """What the gate reconciles without reopening the files (B3, B5, B6, spec 8.1)."""

    schema_version: Literal[1] = 1
    run_id: str
    account: Text
    as_of: date
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    theme_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows_per_file: dict[str, int]
    snapshot_counts: dict[str, int]
    row_ids: dict[str, tuple[str, ...]]
    row_digests: dict[str, tuple[str, ...]]
    refs: dict[str, tuple[str, ...]]
    source_codes_cited: tuple[SourceCode, ...]
    numbers: tuple[CountedNumber, ...]
    count_sentences: tuple[Text, ...]
    tracker_tally: tuple[Text, ...]
    withheld_sentences: int = Field(ge=0)
    long_text_parts: int = Field(ge=0)
    formula_like_cells: int = Field(ge=0)
    files: tuple[FileEntry, ...] = ()
    loop_exits: dict[str, str] = Field(default_factory=dict)
    change_note: str | None = None


class Deliverable(_Frozen):
    """One stored file record (mirrors a ``deliverables`` row)."""

    id: str
    kind: BuildKind
    version: int = Field(ge=1)
    supersedes: str | None
    filename: Text
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    object_key: Text
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    theme_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    built_at: datetime


BuildStatus = Literal["complete", "failed", "not_configured"]


class BuildResult(_Frozen):
    """What ``build_all`` returns: status, the stored deliverables, the manifest and every error."""

    status: BuildStatus
    reason: str | None
    deliverables: tuple[Deliverable, ...] = ()
    manifest: BuildManifest | None = None
    errors: tuple[str, ...] = ()
    change_note: str | None = None
    reused: bool = False
    verbatim_quotes: tuple[str, ...] = ()
