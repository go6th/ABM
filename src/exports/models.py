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

from exports.urlcheck import require_safe_http_url

_CODE = re.compile(r"^S-\d{4,}$")


def _code(value: str) -> str:
    if not _CODE.match(value):
        raise ValueError("a source code looks like S-0001")
    return value


def _url(value: str) -> str:
    return require_safe_http_url(value, "a rendered URL")


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


class LaneMeaningBlock(AuthoredBlock):
    """What the facts likely mean for one lane (Summary section 1, interpretation labeled):
    ``lane`` is the lane's display label."""

    lane: Text


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
    # the Summary's "Platform picture by lane" columns (the reference Summary's section 5): the
    # item (the lane and the attribute the fact answers), how the finding was read, and the
    # reasoning and sources cell (the read, the codes, the confirming question when asked)
    item: str = ""
    read: str = ""
    reasoning: str = ""


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
    # the People List's note for the person (empty when it has none)
    note: str = ""


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
    """One coverage row from the tracker's client view (never typed by the builder). ``needs``,
    ``done`` and ``remains`` are the document's three derived cells (master prompt v2 9.1 item
    11); ``reason``, ``tools`` and ``results`` stay for the client's on-screen table."""

    ref: Ref
    row_id: str
    row: Text
    status: Text
    priority: Text
    reason: str | None
    tools: str
    results: str
    needs: str = ""
    done: str = ""
    remains: str = ""
    pass_no: int = 2


class WaitingRow(_Frozen):
    """A coverage row that waits on the account owner (Blocked or Not done), printed as one
    bullet with its status and what it needs."""

    ref: Ref
    row_id: str
    row: Text
    needs: Text
    status: Text = ""


class GradeLegendRow(_Frozen):
    """One product grade of How to read this file, in the skill's wording
    (``export_rules.yaml`` ``labels.grade_legend``), held to ``config/grading.yaml``'s policy."""

    grade: Text
    meaning: Text
    use: Text


class NewsRow(_Frozen):
    """A dated signal inside the news window: what happened, how it was read, why it matters."""

    ref: Ref
    row_id: str
    date: Text
    what: Text
    read: Text
    codes: tuple[SourceCode, ...] = Field(min_length=1)
    why: Text


class PlatformHiringRow(_Frozen):
    """One platform of the Summary's hiring table: postings naming it over the window, open now
    and by class (the Job Postings workbook's own counts, never recounted)."""

    ref: Ref
    row_id: str
    platform: Text
    postings: int = Field(ge=0)
    open_now: int = Field(ge=0)
    build: int = Field(ge=0)
    admin_owner: int = Field(ge=0)
    working_tool: int = Field(ge=0)
    user: int = Field(ge=0)


class BaselineRow(_Frozen):
    """A tier 0 fact: a baseline company item with what the sources say, printed as one bullet
    ("Item: what (sources).") as the reference Summary lays section 3 out; a finding the policy
    does not let the file state carries its grade in the bullet."""

    row_id: str
    item: Text
    what: Text
    grade_label: Text
    may_state: bool
    codes: tuple[SourceCode, ...] = Field(min_length=1)


class SignalRow(_Frozen):
    """A tier 1 to 3 fact in a signal table, dated by its event date when it has one."""

    ref: Ref
    row_id: str
    finding: Text
    grade_label: Text
    lanes: str
    date: Text
    codes: tuple[SourceCode, ...] = Field(min_length=1)


class SignalTable(_Frozen):
    """One of the four signal tables (tier 1, 2, 3, context and history); may be empty."""

    key: Literal["tier1", "tier2", "tier3", "context"]
    title: Text
    rows: tuple[SignalRow, ...]


class StatusChangeRow(_Frozen):
    """A person whose profile shows a different title from the title search."""

    ref: Ref
    row_id: str
    person: Text
    search_title: str
    profile_title: str
    source_code: SourceCode


class SourceLinkRow(_Frozen):
    """One cited source with its link and domain (Source links). ``title`` is the stored page
    title the link is printed as (the reference links the source name); blank prints the URL."""

    ref: Ref
    code: SourceCode
    url: Url
    domain: Text
    title: str = ""


class SummaryModel(_Frozen):
    """The Account Summary (master prompt v2 section 9.1 order; the fields keep their names)."""

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
    domain: Text | None = None
    version: Text | None = None
    file_names: tuple[Text, ...] = ()
    grade_legend: tuple[GradeLegendRow, ...] = ()
    news: tuple[NewsRow, ...] = ()
    news_sentence: Text | None = None
    baseline: tuple[BaselineRow, ...] = ()
    signals: tuple[SignalTable, ...] = ()
    people_intro: tuple[Text, ...] = ()
    status_changes: tuple[StatusChangeRow, ...] = ()
    waiting: tuple[WaitingRow, ...] = ()
    source_links: tuple[SourceLinkRow, ...] = ()
    platform_hiring: tuple[PlatformHiringRow, ...] = ()
    # the title and opening paragraph as the reference words them (agent 4, 2026-10-26): the
    # account's legal name from the entity lock, the document's own name, the other files' line
    legal_name: Text | None = None
    document_name: Text | None = None
    files_line: Text | None = None
    # the one tally line printed under the tracker tables (computed from the rows)
    tally_line: Text | None = None
    # section 1 of the reference Summary (2026-10-26); empty for runs distilled before it
    stands_out: tuple[AuthoredBlock, ...] = ()
    lane_meanings: tuple[LaneMeaningBlock, ...] = ()
    # the People section's closing "Interpretation" bullets (section kind people_interpretation)
    people_interpretation: tuple[AuthoredBlock, ...] = ()
    # the platform hiring sub-heading with its date window (None: no hiring table)
    platform_hiring_heading: Text | None = None


# ---- Sources and Evidence ------------------------------------------------------------------


class EvidenceUse(_Frozen):
    """One sentence relied on from a source, with the grade of the row it supports."""

    row_id: str
    grade_label: Text
    sentence: Text
    withheld: bool
    # the rendered finding the sentence supports (Part A "What it supports"), when printable
    finding: str = ""
    fact_id: str = ""


class EvidenceSource(_Frozen):
    """One source: stable code, link, date, kind, how it was retrieved and every sentence
    relied on."""

    ref: Ref
    code: SourceCode
    url: Url
    published: date | None
    kind_label: Text
    uses: tuple[EvidenceUse, ...]
    retrieved: Text | None = None
    # the stored page title (printed above the link) and the Type cell: what the link is (a
    # job posting, the company site, a public profile or post) or else the source kind
    title: str = ""
    type_label: str = ""
    # "What it supports" for a source no finding rests on but a posting row cites
    supports_note: str = ""
    # the address of our archived copy of the page (blank: none stored)
    archive_url: str = ""


class JobRead(_Frozen):
    """A job description read in full (Evidence part B), with the posting's own platform
    sentences."""

    ref: Ref
    row_id: str
    title: Text
    url: Url
    code: SourceCode
    posting_ref: str = ""
    posted: str = ""
    entity: str = ""
    sentences: str = ""
    posting_class: str = ""
    # Part B posting cell and Platforms column: the posting's status word, first seen date
    # (ISO) and the lane platforms it names
    status: str = ""
    first_seen: str = ""
    platforms: str = ""
    # the address of our archived copy of the posting (blank: none stored)
    archive_url: str = ""


class LaneReadRow(_Frozen):
    """One posting read in full under one lane platform (Evidence part C). ``posting_ref`` is
    the posting's row in the Job Postings workbook; ``reasoning`` is the class decision's stored
    evidence sentence, else the posting's sentences naming the platform."""

    ref: Ref
    row_id: str
    posting_ref: Ref
    title: Text
    url: Url
    first_seen: Text
    posting_class: Text
    status: Text
    reasoning: Text
    source_code: SourceCode
    archive_url: str = ""


class LaneReadGroup(_Frozen):
    """One lane platform of Evidence part C: the heading counts (postings read in full, postings
    naming the platform, open ones) and the read postings, one row each."""

    platform: Text
    heading: Text
    read: int = Field(ge=0)
    named: int = Field(ge=0)
    open_now: int = Field(ge=0)
    rows: tuple[LaneReadRow, ...] = ()

    @model_validator(mode="after")
    def _counted(self) -> LaneReadGroup:
        if self.read != len(self.rows) or self.read > self.named or self.open_now > self.named:
            raise ValueError("a lane counts its read rows once, within the postings naming it")
        return self


class ExtractQuote(_Frozen):
    """One verbatim extract relied on from a source read in depth (Evidence part D): the quote
    (or the withheld note), who said or wrote it, the grade of the row it supports and what it
    supports. ``dated`` and ``kind`` are a post's own date and type words."""

    row_id: str
    text: Text
    withheld: bool
    code: SourceCode
    speaker: Text | None = None
    grade_label: Text | None = None
    supports: Text | None = None
    kind: Text | None = None
    dated: Text | None = None


class ExtractSource(_Frozen):
    """One source read in depth (Evidence part D), printed as a sub-heading "<name> (<date>)
    (<code>)" with its link and the extracts relied on from it. ``code`` is None for the group of
    the listed people's own posts, whose extracts each cite their own post."""

    row_id: str
    heading: Text
    url: Url | None = None
    code: SourceCode | None = None
    quotes: tuple[ExtractQuote, ...] = Field(min_length=1)


class BoardCaptureRow(_Frozen):
    """One job board count line (Evidence part E)."""

    ref: Ref
    row_id: str
    keyword: Text
    matches: int = Field(ge=0)
    read: Text


class LeadRow(_Frozen):
    """A finding graded Lead, what was tried and what would settle it (Evidence part F)."""

    ref: Ref
    row_id: str
    lead: Text
    queries: Text
    grade_label: Text
    settle: str
    codes: tuple[SourceCode, ...] = Field(min_length=1)


class DataPullRow(_Frozen):
    """One count of a data pull with its base (Evidence part H)."""

    ref: Ref
    row_id: str
    pull: Text
    count: int = Field(ge=0)
    base: Text


class LivePostingRow(_Frozen):
    """One posting captured on the live careers board that names a lane platform (Evidence part
    E): the title, the platforms it names, the entity, how far it was read and its class."""

    ref: Ref
    row_id: str
    posting: Text
    platforms: str
    entity: str
    read: Text
    posting_class: Text
    # the posting's link (the reference links every Part E posting) and our archived copy
    url: Url | None = None
    archive_url: str = ""


class DetectionPullRow(_Frozen):
    """One technology detection of a lane platform (Evidence part H): its first and last seen
    dates and the postings of the Job Postings file that name it."""

    ref: Ref
    row_id: str
    technology: Text
    first_seen: Text
    last_seen: Text
    postings: int = Field(ge=0)


class EntitySplitRow(_Frozen):
    """The postings of the Job Postings file per entity (Evidence part H)."""

    ref: Ref
    row_id: str
    entity: Text
    postings: int = Field(ge=0)


class UrlRow(_Frozen):
    """One source URL written out in full (Evidence part I), with its archived copy's address
    when one is stored."""

    ref: Ref
    code: SourceCode
    url: Url
    archive_url: str = ""


class NewsItem(_Frozen):
    """A news item and whether it was verified at the source article."""

    ref: Ref
    row_id: str
    sentence: Text
    url: Url
    code: SourceCode
    verified: bool


class EvidenceModel(_Frozen):
    """The Sources and Evidence document (parts A to I, master prompt v2 section 9.2)."""

    account: Text
    as_of: date
    sources: tuple[EvidenceSource, ...]
    withheld: int = Field(ge=0)
    job_descriptions: tuple[JobRead, ...]
    news: tuple[NewsItem, ...]
    none_found: Text
    brand: Text | None = None
    # the Account Summary's delivered file name, named in the opening line
    summary_file: str = ""
    # the document title as the reference writes it ("<short name> (<legal name>):
    # Sources and Evidence"); blank prints the file title
    doc_title: str = ""
    lane_reads: tuple[LaneReadGroup, ...] = ()
    extracts: tuple[ExtractSource, ...] = ()
    board_captures: tuple[BoardCaptureRow, ...] = ()
    leads: tuple[LeadRow, ...] = ()
    keep_copies: Text | None = None
    data_pulls: tuple[DataPullRow, ...] = ()
    urls: tuple[UrlRow, ...] = ()
    # parts E and H in the reference shape: live board postings, detections of lane platforms,
    # postings per lane platform, the entity split, the news feed note and the teams note
    live_heading: Text | None = None
    live_postings: tuple[LivePostingRow, ...] = ()
    detections: tuple[DetectionPullRow, ...] = ()
    platform_postings: tuple[PlatformHiringRow, ...] = ()
    entity_split: tuple[EntitySplitRow, ...] = ()
    news_feed_note: Text | None = None
    teams_note: Text | None = None

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
    """One deduplicated senior person (master prompt v2 section 9.3 columns). ``level`` and
    ``lanes`` carry the spec's labels; ``level_id`` and ``lane_id`` the stored ids behind them."""

    ref: Ref
    row_id: str
    number: int = Field(ge=1)
    name: Text
    title: str
    level: str
    level_id: str = ""
    meets_seniority: bool = False
    lanes: str
    lane_id: str | None = None
    priority: str
    former_employers: str = ""
    found_by: str = ""
    profile_url: Url
    second_profile_url: Url | None = None
    location: str = ""
    note: str = ""
    source_code: SourceCode


class SummaryLine(_Frozen):
    """One Summary sheet line: a status and reason with its count, never a name."""

    status: Text
    reason: str
    count: int = Field(ge=0)


class ExcludedPerson(_Frozen):
    """A person excluded by title, named with the title and the reason (the only place the name
    appears)."""

    name: Text
    reason: str
    title: str = ""


class LevelCount(_Frozen):
    """One Level table line: people at the level and how many are priority A."""

    label: Text
    people: int = Field(ge=0)
    priority_a: int = Field(ge=0)


class LaneCount(_Frozen):
    """One Lane table line (senior people only): priority A, B, C and the total."""

    label: Text
    a: int = Field(ge=0)
    b: int = Field(ge=0)
    c: int = Field(ge=0)
    total: int = Field(ge=0)


class PeopleModel(_Frozen):
    """The People List workbook."""

    account: Text
    as_of: date
    rows: tuple[PeopleRow, ...]
    summary: tuple[SummaryLine, ...] = Field(min_length=1)
    sentences: tuple[Text, ...]
    in_scope_listed: int = Field(ge=0)
    # the Summary sheet's title and its paragraphs in the reference's order (what the list
    # merges, rows removed, exclusions by title with names, the seniority rule, the judgment note)
    title_line: str = ""
    paragraphs: tuple[str, ...] = ()
    what: str = ""
    seniority_text: str = ""
    judgment_note: str = ""
    excluded: tuple[ExcludedPerson, ...] = ()
    levels: tuple[LevelCount, ...] = ()
    senior_count: int = Field(ge=0, default=0)
    lanes_table: tuple[LaneCount, ...] = ()

    @model_validator(mode="after")
    def _listed(self) -> PeopleModel:
        if self.in_scope_listed != len(self.rows):
            raise ValueError("the listed count is the number of rows")
        if self.levels and sum(x.people for x in self.levels) != len(self.rows):
            raise ValueError("the Level table counts every listed person once")
        return self


class JobRow(_Frozen):
    """One posting (master prompt v2 section 9.4 columns). ``description`` is the full posting
    text when the posting was read in full, else blank; ``read_text`` is the sentence the reading
    quoted to decide the class (blank when no reading decided it); ``requisition`` is the job ID
    the posting prints (blank when it prints none); ``entity`` names the account or the host."""

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
    posted_or_seen: date | None = None
    entity: str = ""
    source_type: str = ""
    full_text_read: bool = False
    source_code: SourceCode
    requisition: str = ""
    location: str = ""
    read_text: str = ""
    # the reference columns added 2026-10-29: the feed's last seen date, the First seen and Last
    # seen cells as ISO date text, the seniority word as given, our archived copy of the posting
    # (an absolute address; blank when none is stored) and the incremental re-research flag
    # (``new_flag`` is the cell: the Yes word for a new row, blank otherwise)
    last_seen: date | None = None
    first_seen_text: str = ""
    last_seen_text: str = ""
    seniority: str = ""
    archive_url: str = ""
    new_since_previous: bool | None = None
    new_flag: str = ""


class CountLine(_Frozen):
    """A count line on a Counts sheet; ``formula`` marks lines the workbook recomputes (B12)."""

    key: str
    label: Text
    value: int = Field(ge=0)
    base: Text
    formula: Literal["rows", "open", "closed", "none"] = "none"


class PlatformClassCounts(_Frozen):
    """One row of the platform matrix (Job Postings Summary sheet): postings naming a platform,
    open and closed, and by class (formulas)."""

    platform: Text
    rows: int = Field(ge=0)
    by_class: tuple[int, ...]
    open: int = Field(default=0, ge=0)
    closed: int = Field(default=0, ge=0)


class JobsModel(_Frozen):
    """The Job Postings workbook."""

    account: Text
    as_of: date
    rows: tuple[JobRow, ...]
    counts: tuple[CountLine, ...] = Field(min_length=1)
    sentences: tuple[Text, ...]
    class_labels: tuple[Text, ...] = ()
    matrix: tuple[PlatformClassCounts, ...] = ()
    # the Summary sheet: the title line and the explanatory paragraphs (sheet_texts.jobs)
    summary_title: str = ""
    summary_notes: tuple[Text, ...] = ()
    # the entity prefix the Summary matrix counts (``"<prefix>*"`` over the Entity column) and the
    # matrix heading cell; blank prefix: the account name
    prefix: str = ""
    matrix_heading: str = ""

    @model_validator(mode="after")
    def _matrix(self) -> JobsModel:
        if any(len(m.by_class) != len(self.class_labels) for m in self.matrix):
            raise ValueError("every matrix row counts every class once")
        return self


class TechRow(_Frozen):
    """One technology detection with its dates and the postings of the Job Postings file that
    name it; category and lane from the platform dictionary (blank when not one of its
    platforms)."""

    ref: Ref
    row_id: str
    category: str
    technology: Text
    postings: int = Field(ge=0)
    lane: str
    first_seen: str
    last_seen: str
    grade: Text
    source_code: SourceCode


class StackLine(_Frozen):
    """One row of the Confirmed stack sheet: a technology of the organisation's stack under its
    business function and category, the postings count the lookup reports for it, and the lane
    the platform dictionary gives it (blank when the name holds no lane platform word). Out of
    scope platform names are masked before they reach this row."""

    row_id: str
    business_function: Text
    category: Text
    technology: Text
    postings: int = Field(ge=0)
    lane: str


class TeamLine(_Frozen):
    """One row of the Platform teams sheet (the reference's columns). The list columns are the
    reference's ``name (count); name (count)`` text. Printed only when a pipeline stored teams."""

    row_id: str
    team: Text
    # the list columns may be empty (a team whose records name no technology or place)
    lane_technologies: str
    postings: int = Field(ge=0)
    first_activity: str
    last_activity: str
    technologies: str
    locations: str
    job_functions: str


class RankRow(_Frozen):
    """One platform of the Platform Ranking sheet (master prompt v2 section 7.3): the rank and
    share are workbook formulas; ``rank`` and ``share`` hold the values computed in code."""

    platform: Text
    category: Text
    lane: Text
    count: int = Field(ge=0)
    share: float = Field(ge=0, le=1)
    rank: str
    open_now: int = Field(ge=0)
    last_seen: Text
    in_stack: Text
    read_checked: Text


class TechModel(_Frozen):
    """The Tech Stack and Teams workbook."""

    account: Text
    as_of: date
    # the technology detections (a lead each): no sheet prints them, the Evidence file's part H
    # lists them
    rows: tuple[TechRow, ...]
    counts: tuple[CountLine, ...] = Field(min_length=1)
    # the Confirmed stack sheet's rows, in the lookup's own order
    stack: tuple[StackLine, ...] = ()
    teams: tuple[TeamLine, ...] = ()
    # the Evidence file's teams line: "None recorded" or the count of teams listed on the sheet
    teams_note: Text
    # the Platform teams sheet's own note (row 2)
    teams_sheet_note: Text = ""
    ranking: tuple[RankRow, ...] = ()
    seen_once: tuple[RankRow, ...] = ()
    base: int = Field(ge=0, default=0)
    postings_in_window: int = Field(ge=0, default=0)
    rank_counts: tuple[tuple[str, int], ...] = ()
    # the title lines and notes of the three sheets (sheet_texts.tech)
    title: str = ""
    base_line: str = ""
    notes: tuple[Text, ...] = ()
    stack_title: str = ""
    stack_note: str = ""
    teams_title: str = ""

    @model_validator(mode="after")
    def _ranked(self) -> TechModel:
        if any(r.count < 2 or not r.rank for r in self.ranking):
            raise ValueError("a ranked platform has two postings or more and a rank letter")
        if any(r.count != 1 for r in self.seen_once):
            raise ValueError("a platform seen once has exactly one posting")
        return self


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
    # authored blocks left out of the Summary by a content rule, ``where:row_id:rule`` (a dash,
    # an untraceable number, a fact not in the tables): the set is still delivered, the gap is
    # listed here, never silently (one such block used to fail every file, staging 2026-10-21)
    withheld_blocks: tuple[str, ...] = ()
    # text that did not fit one cell: continuation parts on a Long text sheet, cells cut at the
    # limit in a workbook without one, and titles cut at the title limit (all counted here)
    long_text_parts: int = Field(ge=0)
    formula_like_cells: int = Field(ge=0)
    # out-of-scope platform names replaced by the scope mask in rendered text (scope.yaml
    # never_report, master prompt v2 section 0); 0 for a manifest written before the rule
    masked_terms: int = Field(ge=0, default=0)
    # rendered texts replaced by the withheld note because the leak scan fired on them (B9);
    # 0 for a manifest written before the rule (staging 2026-10-25)
    leaks_withheld: int = Field(ge=0, default=0)
    # the same texts by part of the file and client-output rule, ``where:rule=count`` (neutral
    # names, never the text or the term): which part of which file a rule kept a text out of
    texts_withheld: tuple[str, ...] = ()
    # strings the last line of defence replaced in a built model after the final scan of the
    # produced file fired, ``kind:rule=count``: a text path that missed the mapping's checks.
    # Empty in a healthy build; the log names it so the path can be closed
    late_replaced: tuple[str, ...] = ()
    # workspace members whose text was scrubbed of a restricted term, a data tool's name or the
    # word credits, ``member=count``
    members_scrubbed: tuple[str, ...] = ()
    # files the set does not hold, ``kind:rule``: the file could not be made clean row by row (or
    # a section it needs was not written), so that file alone is left out and the rest delivered
    # (the run ends PARTIAL ``files_withheld``); never the text
    files_withheld: tuple[str, ...] = ()
    # external hosts linked or named in the five files, sorted into the four groups of the
    # third-party scan (spec 12): account_and_press, data_tools (always empty in a stored set),
    # aggregators, other
    third_party_domains: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    files: tuple[FileEntry, ...] = ()
    loop_exits: dict[str, str] = Field(default_factory=dict)
    change_note: str | None = None
    # the build's verification checks and what each did, by name: ``passed``, ``not_run: <why>``
    # (no converter installed, a conversion that timed out) or ``failed: <why>`` (a failed check
    # stops the build, so a stored set carries none). A check that did not run is listed here and
    # logged, never silent (fix wave C, 2026-10-29: a 7,000 row workbook outran the converter's
    # timeout and its recalculation check quietly became not_run)
    checks: dict[str, str] = Field(default_factory=dict)
    # the tenant exclusion set the files were built with (``readers.exclusions.exclusion_hash``);
    # a stored file whose hash is not the tenant's current one is rebuilt before it is served
    exclusion_hash: str = Field(default="", pattern=r"^([0-9a-f]{16}|none|)$")


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
    # the rules that failed the build, most specific first (``banned_dash``, not the class)
    codes: tuple[str, ...] = ()
    change_note: str | None = None
    reused: bool = False
    verbatim_quotes: tuple[str, ...] = ()
    # ``kind:rule`` of each file (or the workspace zip) left out of a complete set
    withheld_files: tuple[str, ...] = ()
