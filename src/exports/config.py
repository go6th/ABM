"""Typed configuration for the export builders: the theme and the content rules.

Both files are validated when loaded (fail fast at startup) and hashed; the hashes are recorded on
every deliverable so a build is always traceable to the exact look and rules it used. This module
is the only place a style value is read; no renderer holds a style literal (static test).
GUARANTEE(test_every_config_entry_names_its_source_or_status)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from account_intelligence.config import CONFIG_DIR
from exports.errors import ConfigError
from exports.models import FileKind

ColumnKind = Literal[
    "ref", "code", "label", "text_short", "text_long", "url", "date", "count", "percent"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _hex(value: str) -> str:
    if len(value) != 6 or any(c not in "0123456789ABCDEFabcdef" for c in value):
        raise ValueError(f"colour {value!r} is not six hex digits")
    return value.upper()


Hex = Annotated[str, AfterValidator(_hex)]


class Font(_Strict):
    """One font: face, size, weight, slant and colour."""

    name: str = Field(min_length=1)
    size_pt: float = Field(gt=0, le=72)
    bold: bool
    italic: bool
    color: Hex


class Margins(_Strict):
    """Page margins in inches."""

    top: float = Field(ge=0.25, le=3)
    bottom: float = Field(ge=0.25, le=3)
    left: float = Field(ge=0.25, le=3)
    right: float = Field(ge=0.25, le=3)


class Page(_Strict):
    """Page size and margins."""

    width_in: float = Field(gt=0)
    height_in: float = Field(gt=0)
    margins_in: Margins
    source: str


class WordFonts(_Strict):
    """Every font the Word renderer may use; nothing else is ever set."""

    body: Font
    title: Font
    subtitle: Font
    heading1: Font
    heading2: Font
    quote: Font
    note: Font
    table_header: Font
    table_body: Font
    header: Font
    footer: Font


class WordTable(_Strict):
    """Table styling."""

    header_fill: Hex
    border_color: Hex
    border_size_eighth_pt: int = Field(ge=2, le=96)
    cell_margin_twips: int = Field(ge=0, le=1440)
    repeat_header_row: bool
    row_cant_split: bool


class Spacing(_Strict):
    """Paragraph spacing in points."""

    paragraph_after_pt: float = Field(ge=0, le=72)
    heading_before_pt: float = Field(ge=0, le=72)
    heading_after_pt: float = Field(ge=0, le=72)


TableName = Literal[
    "platform_picture",
    "key_people",
    "linkedin",
    "tracker",
    "coverage",
    "waiting",
    "grade_legend",
    "recent_news",
    "signals",
    "status_changed",
    "source_links",
    "platform_hiring",
    "sources",
    "job_reads",
    "lane_reads",
    "board_captures",
    "live_postings",
    "leads",
    "data_pulls",
    "tech_detections",
    "platform_postings",
    "entity_split",
    "urls",
]


class WordTheme(_Strict):
    """The Word look."""

    page: Page
    fonts: WordFonts
    table: WordTable
    tables: dict[TableName, tuple[float, ...]]
    spacing: Spacing

    @model_validator(mode="after")
    def _proportions(self) -> WordTheme:
        for name, widths in self.tables.items():
            if abs(sum(widths) - 100) > 0.01 or min(widths) <= 0:
                raise ValueError(f"table {name}: proportions must be positive and sum to 100")
        return self


class ExcelFonts(_Strict):
    """Every font the workbook renderer may use."""

    title: Font
    header: Font
    body: Font
    note: Font


class ColumnStyle(_Strict):
    """Width, number format and wrapping of one column kind."""

    width: float = Field(gt=0, le=255)
    number_format: str = Field(min_length=1)
    wrap: bool


class ExcelPrint(_Strict):
    """Print setup."""

    orientation: Literal["landscape", "portrait"]
    paper: Literal["letter"]
    fit_to_width: bool


class ExcelTheme(_Strict):
    """The workbook look."""

    fonts: ExcelFonts
    header_fill: Hex
    summary_fill: Hex
    border_color: Hex
    freeze_header: bool
    autofilter: bool
    column_kinds: dict[ColumnKind, ColumnStyle]
    print: ExcelPrint

    @model_validator(mode="after")
    def _all_kinds(self) -> ExcelTheme:
        missing = set(ColumnKind.__args__) - set(self.column_kinds)  # type: ignore[attr-defined]
        if missing:
            raise ValueError(f"column kinds without a style: {sorted(missing)}")
        return self


DosTime = tuple[int, int, int, int, int, int]


class ZipTheme(_Strict):
    """Package settings that affect bytes (B10)."""

    compression: Literal["deflate", "stored"]
    entry_time: DosTime

    @field_validator("entry_time")
    @classmethod
    def _dos_time(cls, v: DosTime) -> DosTime:
        if v[0] < 1980:
            raise ValueError("a zip entry time cannot be before 1980")
        return v


class Theme(_Strict):
    """``config/export_theme.yaml`` (status: proposal; D-26)."""

    version: str
    status: Literal["proposal", "sourced"]
    decision: str
    source: str = Field(min_length=1)
    word: WordTheme
    excel: ExcelTheme
    zip: ZipTheme


# ---- content rules -------------------------------------------------------------------------


class Attribution(_Strict):
    """Client-facing attribution."""

    name: str = Field(min_length=1)
    source: str


class DocumentProperties(_Strict):
    """The only values written into document properties."""

    author: str
    company: str
    source: str


class SensitiveSentence(_Strict):
    """The standing sentence on sensitive findings."""

    text: str = Field(min_length=1)
    source: str
    status: str


class BannedDashes(_Strict):
    """Banned dash characters and the scope they apply to."""

    scope: Literal["authored_prose"]
    characters: tuple[str, ...] = Field(min_length=1)
    characters_source: str
    spaced_hyphen_is_punctuation: bool
    spaced_hyphen_status: str


class StalePhrases(_Strict):
    """Stale phrases (empty until the product owner supplies the list)."""

    source: str
    phrases: tuple[str, ...]


class ExcludedNames(_Strict):
    """Excluded names (empty until the product owner supplies the list)."""

    source: str
    names: tuple[str, ...]


class DeliveryConfig(_Strict):
    """Delivery gate configuration."""

    enabled: bool = False
    source: str
    render_checks: bool = False
    render_checks_source: str


class Abbreviation(_Strict):
    """One abbreviation and its expansion."""

    abbr: str = Field(min_length=2)
    expansion: str = Field(min_length=3)


class Abbreviations(_Strict):
    """Abbreviations written out on first use."""

    source: str
    entries: tuple[Abbreviation, ...]


class PartnerRule(_Strict):
    """Framings that fail a build for a Salesforce-partner tenant."""

    framings: tuple[str, ...] = Field(min_length=1)
    framings_source: str
    more_framings_source: str


class QuoteHandling(_Strict):
    """What happens to a verbatim quote that contains a restricted term (D-24)."""

    policy: Literal["withhold_and_count"]
    withheld_text: str
    status: str


class RowCaps(_Strict):
    """Row caps per file; reaching one fails the build (B3)."""

    summary_lane_rows: int = Field(gt=0)
    evidence_uses: int = Field(gt=0)
    people: int = Field(gt=0)
    postings: int = Field(gt=0)
    detections: int = Field(gt=0)
    sections: int = Field(gt=0)
    tracker_rows: int = Field(gt=0)


class Limits(_Strict):
    """Cell, row and text limits."""

    excel_cell_chars: int = Field(gt=100)
    excel_cell_chars_source: str
    row_caps: RowCaps
    row_caps_status: str
    keyset_batch: int = Field(gt=0, le=10_000)
    keyset_batch_status: str
    authored_text_chars: int = Field(gt=0)


class RendererLimits(_Strict):
    """The Word renderer subprocess bounds (D-26)."""

    timeout_s: float = Field(gt=0, le=600)
    max_output_bytes: int = Field(gt=0)
    max_stderr_bytes: int = Field(gt=0)
    reruns: int = Field(ge=0, le=1)
    status: str


class SummaryHeadings(_Strict):
    """Account Summary section headings, in the document order (master prompt v2 section 9.1)."""

    files: str
    how_to_read: str
    narrative: str
    narrative_picture: str
    stands_out: str
    lane_meaning: str
    caveats: str
    executive_voice: str
    news: str
    baseline: str
    signals: str
    signals_tier1: str
    signals_tier2: str
    signals_tier3: str
    signals_context: str
    lane_tables: str
    platform_hiring: str
    people: str
    people_method: str
    key_people: str
    linkedin_findings: str
    status_changed: str
    hypotheses: str
    sensitive_cautions: str
    first_call_questions: str
    decision_changes: str
    coverage_tracker: str
    tracker_waiting: str
    tracker_pass1: str
    tracker_pass2: str
    source_links: str
    unassigned_lane: str
    absence: str


class EvidenceHeadings(_Strict):
    """Sources and Evidence part headings, A to I (master prompt v2 section 9.2)."""

    sources: str
    job_descriptions: str
    careers_postings: str
    post_extracts: str
    board_captures: str
    leads: str
    keep_copies: str
    data_pulls: str
    urls: str
    # sub-headings of parts E and H; ``live_postings`` takes ``{dates}``
    live_postings: str
    live_postings_undated: str
    tech_detections: str
    platform_postings: str
    entity_split: str
    news_feed: str
    teams: str


class Headings(_Strict):
    """Headings per document."""

    source: str
    summary: SummaryHeadings
    evidence: EvidenceHeadings


class GradeLegendText(_Strict):
    """One grade of How to read this file, as the skill's output specification words it."""

    meaning: str = Field(min_length=1)
    use: str = Field(min_length=1)


class DocumentLabels(_Strict):
    """Fixed words the Word documents print around the data."""

    sources_suffix: str
    question_prefix: str
    speaker_prefix: str
    no_sentence: str
    not_dated: str
    verified: str
    feed_only: str
    as_of: str
    attribution_line: str
    sensitive_heading: str
    # title block, Recent news, coverage tracker and Sources and Evidence words (master prompt
    # v2 sections 9.1, 9.2); ``remains`` maps a client status word to the "What remains" cell
    research_by: str
    domain_prefix: str
    version_prefix: str
    config_prefix: str
    files_intro: str
    summary_title: str
    summary_title_plain: str
    research_date: str
    files_line: str
    read_full: str
    read_headline: str
    read_feed: str
    read_cached: str
    read_not: str
    news_sentence: str
    # How to read this file: the line above the legend and the skill's wording per grade
    legend_intro: str
    grade_legend: dict[Literal["confirmed", "probable", "hypothesis", "lead"], GradeLegendText]
    caution_prefix: str
    needs_nothing: str
    done_results: str
    done_uncounted: str
    done_nothing: str
    done_zero: str
    done_unrecorded: str
    remains: dict[str, str]
    waiting_line: str
    waiting_line_plain: str
    tally_line: str
    source_links_note: str
    retrieved_live: str
    retrieved_cached: str
    retrieved_feed: str
    retrieved_snippet: str
    retrieved_link: str
    evidence_opening: str = Field(min_length=1)
    supports_quote: str = Field(min_length=1)
    source_type_posting: str = Field(min_length=1)
    source_type_site: str = Field(min_length=1)
    source_type_profile: str = Field(min_length=1)
    source_type_post: str = Field(min_length=1)
    posting_first_seen: str = Field(min_length=1)
    posting_supports: str = Field(min_length=1)
    job_reads_intro: str = Field(min_length=1)
    post_type: str
    # Evidence part D: the sub-heading suffix of a source the Summary quotes an executive from,
    # the sub-heading of the listed people's own posts and the word before what an extract
    # supports
    extract_voice: str
    extract_posts: str
    supports_prefix: str
    queries_not_tried: str
    keep_copies_text: str
    # Evidence parts E, F and H words: how far a live posting was read, what a Lead's stored
    # verification status says was tried ({queries}, {tools}: the Lead rule numbers of
    # config/grading.yaml), the detections intro and the news feed lines
    read_in_full_yes: str
    read_platform_sentence: str
    read_title_only: str
    lead_queries: dict[Literal["pending", "incomplete", "complete", "upgraded"], str]
    detections_intro: str
    news_feed_counted: str
    news_feed_uncounted: str
    news_feed_none: str


class Documents(_Strict):
    """Word table headers and labels."""

    source: str
    tables: dict[TableName, tuple[str, ...]]
    labels: DocumentLabels
    status: str


class StatementText(_Strict):
    """The statement-policy column text."""

    may_state: str
    ask: str
    status: str


class NoneFound(_Strict):
    """Explicit empty-section text (B3)."""

    text: str
    teams: str
    source: str


class Labels(_Strict):
    """Display words for stored vocabularies (master prompt v2 sections 9.3 to 9.5)."""

    posting_class: dict[str, str]
    posting_class_confirming: tuple[str, ...] = Field(min_length=1)
    posting_status: dict[str, str]
    posting_source: dict[str, str]
    entity_to_confirm_suffix: str = Field(min_length=1)
    people_level: dict[str, str]
    people_level_order: tuple[str, ...] = Field(min_length=1)
    people_level_senior: tuple[str, ...] = Field(min_length=1)
    # the People List's own lane wording (falls back to ``lane``)
    people_lane: dict[str, str] = Field(default_factory=dict)
    lane: dict[str, str]
    lane_none: str = Field(min_length=1)
    lane_outside: str = Field(min_length=1)
    found_by: dict[str, str]
    yes: str = Field(min_length=1)
    no: str = Field(min_length=1)
    not_pulled: str = Field(min_length=1)
    not_dated: str = Field(min_length=1)
    teams_none: str = Field(min_length=1)
    source_kind: dict[str, str]
    detection_grade: str
    source: str

    @model_validator(mode="after")
    def _vocabularies(self) -> Labels:
        classes = {"build", "admin_owner", "working_tool", "user", "unclassified"}
        derived = {"no_lane_platform", "title_only"}
        if set(self.posting_class) != classes | derived:
            raise ValueError("posting_class labels must cover the five classes and the two derived")
        if not set(self.posting_class_confirming) <= classes:
            raise ValueError("posting_class_confirming names classes the pipeline does not have")
        if set(self.posting_status) != {"open", "closed", "unknown"}:
            raise ValueError("posting_status labels must cover open, closed and unknown")
        if set(self.posting_source) != {"live_board_read", "feed_read", "feed_title_only"}:
            raise ValueError("posting_source labels must cover the three read types")
        if set(self.source_kind) != {"primary", "secondary", "aggregator"}:
            raise ValueError("source_kind labels must cover the sources.kind vocabulary")
        if "other" not in self.people_level:
            raise ValueError("people_level needs an 'other' label")
        if set(self.people_level.values()) != set(self.people_level_order):
            raise ValueError("people_level_order must list every level label once")
        if not set(self.people_level_senior) <= set(self.people_level):
            raise ValueError("people_level_senior names a level id without a label")
        if set(self.found_by) != {"title_search", "association", "mention"}:
            raise ValueError("found_by labels must cover the three people origins")
        return self

    def level_label(self, level: str | None) -> str:
        """The spec's level label of a rubric level id (``senior manager`` and ``senior_manager``
        both resolve); anything unknown is the ``other`` label."""
        key = (level or "").strip().casefold().replace(" ", "_")
        return self.people_level.get(key, self.people_level["other"])

    def meets_seniority(self, level: str | None) -> bool:
        """True when the level id is one the seniority rule accepts."""
        key = (level or "").strip().casefold().replace(" ", "_")
        return key in self.people_level_senior

    def lane_label(self, lane: str | None) -> str:
        """The spec's lane label of a lane id; no lane (or an unknown one) is ``lane_none``."""
        key = (lane or "").strip().casefold().replace(" ", "_")
        return self.lane.get(key, self.lane_none)

    def people_lane_label(self, lane: str | None) -> str:
        """The People List's lane label (``people_lane``, else ``lane_label``)."""
        key = (lane or "").strip().casefold().replace(" ", "_")
        return self.people_lane.get(key) or self.lane_label(lane)


class PeopleSheetTexts(_Strict):
    """Fixed sentences of the People List Summary sheet, in the reference's order: the title,
    what the list merges and from which searches, the rows removed before the list, the people
    excluded by title with their names, the seniority rule and the judgment note. Placeholders
    ``{rows}``, ``{searches}``, ``{records}`` and ``{people}`` carry a stored count with its
    word; no number is typed here."""

    title: str = Field(min_length=1)
    what: str = Field(min_length=1)
    merges: str = Field(min_length=1)
    merge_title: str = Field(min_length=1)
    merge_title_any: str = Field(min_length=1)
    merge_association: str = Field(min_length=1)
    merge_mention: str = Field(min_length=1)
    searches: str = Field(min_length=1)
    matches: str = Field(min_length=1)
    on_list: str = Field(min_length=1)
    dedupe: str = Field(min_length=1)
    removed: str = Field(min_length=1)
    removed_items: dict[str, str]
    removed_other: str = Field(min_length=1)
    withheld: str = Field(min_length=1)
    exclusions: str = Field(min_length=1)
    exclusion_reasons: dict[str, str]
    no_exclusions: str = Field(min_length=1)
    seniority_prefix: str = Field(min_length=1)
    seniority_filter: str = Field(min_length=1)
    judgment_note: str = Field(min_length=1)
    level_heading: str = Field(min_length=1)
    senior_label: str = Field(min_length=1)
    lane_heading: str = Field(min_length=1)
    total_label: str = Field(min_length=1)
    link_text: str = Field(min_length=1)


class TechSheetTexts(_Strict):
    """Fixed sentences of the Platform Ranking, stack and Platform teams sheets (the reference
    file set's wording)."""

    title: str = Field(min_length=1)
    base_line: str = Field(min_length=1)
    rank_rule: str = Field(min_length=1)
    count_rule: str = Field(min_length=1)
    signal_rule: str = Field(min_length=1)
    research_by: str = Field(min_length=1)
    counts_heading: str = Field(min_length=1)
    seen_once_heading: str = Field(min_length=1)
    rank_label: str = Field(min_length=1)
    stack_title: str = Field(min_length=1)
    stack_note: str = Field(min_length=1)
    teams_title: str = Field(min_length=1)


class JobsSheetTexts(_Strict):
    """Fixed sentences of the Job Postings Summary sheet (the reference file's Summary)."""

    title: str = Field(min_length=1)
    paragraphs: tuple[str, ...] = Field(min_length=1)
    matrix_heading: str = Field(min_length=1)
    total: str = Field(min_length=1)
    open: str = Field(min_length=1)
    closed: str = Field(min_length=1)
    rows_all: str = Field(min_length=1)
    rows_account: str = Field(min_length=1)
    rows_other: str = Field(min_length=1)


class SheetTexts(_Strict):
    """``sheet_texts``: the words the workbook Summary sheets print around the numbers."""

    source: str
    people: PeopleSheetTexts
    tech: TechSheetTexts
    jobs: JobsSheetTexts


class EvidenceRules(_Strict):
    """Evidence file selection rules."""

    news_attribute_prefixes: tuple[str, ...]
    news_source: str
    profile_hosts: tuple[str, ...] = ()
    profile_paths: tuple[str, ...] = ()
    post_paths: tuple[str, ...] = ()
    lane_read_heading: str = Field(min_length=1)
    lane_read_posting: str = Field(min_length=1)
    lane_read_none: str = Field(min_length=1)
    lane_read_note: str = Field(min_length=1)
    lane_read_source: str = ""


class Column(_Strict):
    """One workbook column."""

    key: str
    header: str = Field(min_length=1)
    kind: ColumnKind


class PeopleBook(_Strict):
    """People List workbook layout."""

    sheet: str
    columns: tuple[Column, ...] = Field(min_length=1)
    summary_sheet: str


class JobsBook(_Strict):
    """Job Postings workbook layout."""

    sheet: str
    columns: tuple[Column, ...] = Field(min_length=1)
    counts_sheet: str
    long_text_sheet: str
    matrix_platforms: tuple[str, ...] = Field(min_length=1)
    matrix_all_rows: str = Field(min_length=1)
    # the sheet printed first: title, explanatory paragraphs and the platform matrix
    summary_sheet: str = Field(min_length=1)
    # the job ID label a posting prints and the ID itself (group 1): the Requisition column
    requisition_pattern: str = Field(min_length=1)

    @field_validator("requisition_pattern")
    @classmethod
    def _one_group(cls, v: str) -> str:
        if re.compile(v).groups != 1:
            raise ValueError("requisition_pattern needs exactly one group (the ID)")
        return v


class TechBook(_Strict):
    """Tech Stack and Teams workbook layout."""

    sheet: str
    columns: tuple[Column, ...] = Field(min_length=1)
    teams_sheet: str
    teams_columns: tuple[Column, ...] = Field(min_length=1)
    ranking_sheet: str
    ranking_columns: tuple[Column, ...] = Field(min_length=1)


class Workbooks(_Strict):
    """All workbook layouts."""

    source: str
    people: PeopleBook
    jobs: JobsBook
    tech: TechBook

    @model_validator(mode="after")
    def _ref_column(self) -> Workbooks:
        for book in (self.people, self.jobs, self.tech):
            keys = [c.key for c in book.columns]
            if "ref" not in keys or len(set(keys)) != len(keys):
                raise ValueError(f"{book.sheet}: a ref column is present and keys are unique")
        ranking = [c.key for c in self.tech.ranking_columns]
        if len(set(ranking)) != len(ranking) or not {"rank", "count", "share"} <= set(ranking):
            raise ValueError("the ranking columns carry rank, count and share, keys unique")
        return self

    def ref_column(self, book: PeopleBook | JobsBook | TechBook) -> int:
        """1-based index of the row-key column of a sheet (row conservation re-reads it)."""
        return [c.key for c in book.columns].index("ref") + 1


class RawPayloads(_Strict):
    """Raw broker payloads in the zip (D-25: excluded until D-10 is answered)."""

    default: Literal["excluded"]
    neutral_name_copy_enabled: bool


_PROVIDER_LIKE = re.compile(r"\bp_[a-z]+\b|\bcredits?\b", re.IGNORECASE)


def _no_vendor_words(values: Iterable[str], what: str) -> None:
    """A role label or text is shown to the client: it never carries a provider code or the
    word the third-party scan refuses ("credits")."""
    for value in values:
        if not value.strip() or _PROVIDER_LIKE.search(value):
            raise ValueError(f"{what} {value!r} is empty or names a provider or credits")


class PullLogRules(_Strict):
    """``pull_log.md`` (master prompt v2 section 9.6): one table line per distinct connector call
    and a completeness note. The tool role words live here, never in code: a client file names
    what a tool does, not who sells it."""

    title: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    columns: tuple[str, str, str, str, str, str]
    roles: dict[str, str] = Field(min_length=1)
    default_role: str = Field(min_length=1)
    # the operation's deliverable kind: the file its results feed
    outputs: dict[str, FileKind]
    default_output: str = Field(min_length=1)
    not_counted: str = Field(min_length=1)
    repeated: str = Field(min_length=1)
    none_recorded: str = Field(min_length=1)
    completeness_heading: str = Field(min_length=1)
    calls_total: str = Field(min_length=1)
    unique_queries: str = Field(min_length=1)
    duplicate_queries: str = Field(min_length=1)
    zero_result_queries: str = Field(min_length=1)
    failed_calls: str = Field(min_length=1)
    lock_recorded: str = Field(min_length=1)
    lock_missing: str = Field(min_length=1)

    @model_validator(mode="after")
    def _roles_are_neutral(self) -> PullLogRules:
        _no_vendor_words(
            [*self.roles.values(), self.default_role, self.intro, *self.columns], "pull log text"
        )
        if set(self.outputs) - set(self.roles):
            raise ValueError("every output operation needs a tool role")
        return self


class EntityLockFileRules(_Strict):
    """``data/entity_lock.json`` when the run recorded no lock."""

    none_recorded: str = Field(min_length=1)


class WebSearchFileRules(_Strict):
    """``data/web_searches.json``: why a search's results were all refused."""

    dropped_entity: str = Field(min_length=1)
    dropped_empty: str = Field(min_length=1)


class SenseCheckFileRules(_Strict):
    """``notes/sense_check.md``."""

    none_recorded: str = Field(min_length=1)
    recorded: str = Field(min_length=1)
    conflicts_heading: str = Field(min_length=1)
    conflict_line: str = Field(min_length=1)
    conflicts_none: str = Field(min_length=1)


class Pass1FileRules(_Strict):
    """``notes/pass1.md``: the tracker rows whose reason says the client's own mail, notes and
    files were not switched on decide the line."""

    row_keys: tuple[str, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    skipped: str = Field(min_length=1)
    read: str = Field(min_length=1)
    unknown: str = Field(min_length=1)


class RestoreRules(_Strict):
    """``RESTORE.md``, after the owner's reference workspace: the layout, how to rebuild, the
    canonical query definitions written from the run's own counts and the entity rules applied.
    Every sentence lives here; the figures come from the run."""

    title: str = Field(min_length=1)
    intro: str = Field(min_length=1)
    layout_heading: str = Field(min_length=1)
    folders: dict[str, str] = Field(min_length=1)
    folder_line: str = Field(min_length=1)
    manifest_line: str = Field(min_length=1)
    restore_heading: str = Field(min_length=1)
    restore_steps: tuple[str, ...] = Field(min_length=1)
    queries_heading: str = Field(min_length=1)
    query_platform: str = Field(min_length=1)
    query_rank: str = Field(min_length=1)
    rank_part: str = Field(min_length=1)
    rank_none: str = Field(min_length=1)
    query_class: str = Field(min_length=1)
    query_count: str = Field(min_length=1)
    query_senior: str = Field(min_length=1)
    query_zero: str = Field(min_length=1)
    entity_heading: str = Field(min_length=1)
    entity_none: str = Field(min_length=1)
    entity_domains: str = Field(min_length=1)
    entity_careers: str = Field(min_length=1)
    entity_subsidiaries: str = Field(min_length=1)
    entity_lookalikes: str = Field(min_length=1)
    out_of_scope: str = Field(min_length=1)
    window: str = Field(min_length=1)

    @model_validator(mode="after")
    def _neutral(self) -> RestoreRules:
        _no_vendor_words([self.intro, *self.folders.values(), *self.restore_steps], "restore text")
        return self


class WorkspaceRules(_Strict):
    """Workspace zip contents and caps (D-25)."""

    decision: str
    status: str
    restore_text_source: str
    restore: RestoreRules
    # the folders under the root, in zip order (the reference workspace's names)
    folders: tuple[str, ...] = Field(min_length=1)
    raw_payloads: RawPayloads
    max_members: int = Field(gt=0, le=10_000)
    max_member_bytes: int = Field(gt=0)
    max_total_bytes: int = Field(gt=0)
    entry_name_max: int = Field(gt=8, le=255)
    # the members built from stored run data beyond the five files and their render data, in
    # zip order; a zip missing one fails its check (master prompt v2 sections 2 and 9.6)
    required_members: tuple[str, ...] = Field(min_length=1)
    pull_log: PullLogRules
    entity_lock: EntityLockFileRules
    web_searches: WebSearchFileRules
    sense_check: SenseCheckFileRules
    pass1: Pass1FileRules

    @model_validator(mode="after")
    def _members_are_distinct(self) -> WorkspaceRules:
        if len(set(self.required_members)) != len(self.required_members):
            raise ValueError("required_members repeats a name")
        reserved = {"RESTORE.md", "MANIFEST.json"}
        if reserved & set(self.required_members):
            raise ValueError("required_members cannot restate a fixed member")
        if any(n.split("/", 1)[0] not in self.folders for n in self.required_members):
            raise ValueError("every required member sits in one of the workspace folders")
        if set(self.folders) != set(self.restore.folders):
            raise ValueError("RESTORE.md describes every workspace folder, and only those")
        return self


class ContentRules(_Strict):
    """``config/export_rules.yaml``: D-24 and D-25 proposals with sourced entries."""

    version: str
    status: str
    decision: str
    attribution: Attribution
    document_properties: DocumentProperties
    sensitive_sentence: SensitiveSentence
    banned_dashes: BannedDashes
    stale_phrases: StalePhrases
    excluded_names: ExcludedNames
    delivery: DeliveryConfig
    abbreviations: Abbreviations
    partner_rule: PartnerRule
    quote_handling: QuoteHandling
    limits: Limits
    renderer: RendererLimits
    headings: Headings
    documents: Documents
    statement_text: StatementText
    none_found: NoneFound
    labels: Labels
    sheet_texts: SheetTexts
    evidence: EvidenceRules
    workbooks: Workbooks
    workspace: WorkspaceRules


class ScopeRules(_Strict):
    """What the export layer reads from ``config/skill/scope.yaml`` (master prompt v2 section 0):
    the platforms never named in a client file and the mask that replaces them, the seniority
    rule sentence and the posting window."""

    never_report: tuple[str, ...]
    mask: str = Field(min_length=1)
    seniority_rule: str = Field(min_length=1)
    postings_window_months: int = Field(gt=0)
    lanes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _plain(self) -> ScopeRules:
        if any(not n.strip() for n in self.never_report):
            raise ValueError("a never_report name is blank")
        if any(n.casefold() in self.mask.casefold() for n in self.never_report):
            raise ValueError("the mask names a never_report platform")
        return self


class PlatformEntry(_Strict):
    """One platform of ``config/platform_dictionary.yaml`` with its compiled pattern."""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    lane: str = ""
    pattern: str = Field(min_length=1)
    case_sensitive: bool = False

    @property
    def regex(self) -> re.Pattern[str]:
        """The compiled pattern (compiled once per entry)."""
        return _compiled(self.pattern, self.case_sensitive)

    def names(self, text: str) -> bool:
        """True when ``text`` names the platform."""
        return bool(self.regex.search(text))


@lru_cache(maxsize=256)
def _compiled(pattern: str, case_sensitive: bool) -> re.Pattern[str]:
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)


class Lane(_Strict):
    """One platform lane (``config/lanes.yaml``, PRD s3.3): stored id and displayed label."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    label: str


class ExportConfig(_Strict):
    """Theme, rules, lane labels and file names, with content hashes recorded on every deliverable.
    ``rules_hash`` covers ``export_rules.yaml``, ``lanes.yaml`` and ``output_spec.yaml``."""

    theme: Theme
    rules: ContentRules
    lanes: tuple[Lane, ...] = Field(min_length=1)
    filenames: dict[str, str]
    theme_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: ScopeRules
    platforms: tuple[PlatformEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _tables(self) -> ExportConfig:
        widths, headers = self.theme.word.tables, self.rules.documents.tables
        if set(widths) != set(headers):
            raise ValueError("theme word.tables and rules documents.tables name different tables")
        for name in widths:
            if len(widths[name]) != len(headers[name]):
                raise ValueError(
                    f"table {name}: {len(headers[name])} headers, {len(widths[name])} proportions"
                )
        return self

    def lane_label(self, lane_id: str | None) -> str | None:
        """The client label of a lane id: the spec's label (``labels.lane``) when the lane has
        one, else the lanes.yaml label; None when the id is unknown. The spec's label first, so a
        never_report name in a lanes.yaml label never reaches a client file."""
        spec = self.rules.labels.lane.get(lane_id or "")
        if spec:
            return spec
        return next((lane.label for lane in self.lanes if lane.id == lane_id), None)

    def lane_rank(self, lane_id: str | None) -> int:
        """Position of the lane in the configured order; unknown lanes sort last."""
        ids = [lane.id for lane in self.lanes]
        return ids.index(lane_id) if lane_id in ids else len(ids)


def _load(path: Path) -> tuple[object, str]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ConfigError(f"cannot read {path.name}: {error.strerror}") from error
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def _scope_rules(doc: object) -> ScopeRules:
    """``ScopeRules`` from the parsed scope.yaml (``value`` keys as agents.config reads them)."""
    try:
        d: dict[str, Any] = doc  # type: ignore[assignment]
        return ScopeRules(
            never_report=tuple(str(x) for x in d["never_report"]["value"]),
            mask=str(d["never_report"]["mask"]),
            seniority_rule=str(d["seniority"]["rule"]["value"]),
            postings_window_months=int(d["windows_months"]["postings"]["value"]),
            lanes=tuple(str(x) for x in d["lanes"]["value"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"scope.yaml is invalid for the exports: {error}") from error


def _platforms(doc: object, scope: ScopeRules) -> tuple[PlatformEntry, ...]:
    """The platform dictionary, validated: every pattern compiles, every lane is a scope lane or
    empty, no name repeats and no never_report name is a platform or hides in one."""
    try:
        entries = tuple(PlatformEntry.model_validate(x) for x in doc["platforms"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"platform_dictionary.yaml is invalid: {error}") from error
    if not entries:
        raise ConfigError("platform_dictionary.yaml names no platform")
    names = [e.name.casefold() for e in entries]
    if len(set(names)) != len(names):
        raise ConfigError("platform_dictionary.yaml names a platform twice")
    for e in entries:
        try:
            _ = e.regex
        except re.error as error:
            raise ConfigError(f"platform {e.name}: pattern does not compile ({error})") from error
        if e.lane and e.lane not in scope.lanes:
            raise ConfigError(f"platform {e.name}: lane {e.lane} is not a scope lane")
        for never in scope.never_report:
            if never.casefold() in e.name.casefold() or e.names(never):
                raise ConfigError(f"platform {e.name}: a never_report platform is never ranked")
    return entries


def load_export_config(directory: Path | None = None) -> ExportConfig:
    """Validate every file; any invalid or missing value raises ``ConfigError`` (no default is
    invented for a bad value). ``directory`` overrides the theme, rules, lanes and output spec;
    the scope and the platform dictionary are read from it when present, else from the
    repository configuration."""
    d = directory or CONFIG_DIR
    theme_data, theme_hash = _load(d / "export_theme.yaml")
    rules_data, rules_digest = _load(d / "export_rules.yaml")
    lanes_data, lanes_digest = _load(d / "lanes.yaml")
    spec_data, spec_digest = _load(d / "output_spec.yaml")
    scope_path = d / "skill" / "scope.yaml"
    if not scope_path.exists():
        scope_path = CONFIG_DIR / "skill" / "scope.yaml"
    platforms_path = d / "platform_dictionary.yaml"
    if not platforms_path.exists():
        platforms_path = CONFIG_DIR / "platform_dictionary.yaml"
    scope_data, scope_digest = _load(scope_path)
    platforms_data, platforms_digest = _load(platforms_path)
    rules_hash = hashlib.sha256(
        f"{rules_digest}{lanes_digest}{spec_digest}{scope_digest}{platforms_digest}".encode()
    ).hexdigest()
    scope = _scope_rules(scope_data)
    try:
        filenames = {
            str(item["id"]): str(item["filename"])
            for item in spec_data["deliverables"]  # type: ignore[index]
        }
        return ExportConfig(
            theme=Theme.model_validate(theme_data),
            rules=ContentRules.model_validate(rules_data),
            lanes=tuple(Lane.model_validate(x) for x in lanes_data["lanes"]),  # type: ignore[index]
            filenames=filenames,
            theme_hash=theme_hash,
            rules_hash=rules_hash,
            scope=scope,
            platforms=_platforms(platforms_data, scope),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"export configuration is invalid: {error}") from error


@lru_cache(maxsize=1)
def default_export_config() -> ExportConfig:
    """The repository configuration, loaded once per process."""
    return load_export_config()


@lru_cache(maxsize=1)
def provider_codes() -> tuple[str, ...]:
    """The provider codes of ``config/providers.yaml`` (``p_search``...): the internal names a
    client file must never carry. Read once; the list is never retyped in code."""
    import yaml

    doc = yaml.safe_load((CONFIG_DIR / "providers.yaml").read_text())
    block = doc
    if isinstance(doc, dict) and isinstance(doc.get("providers"), dict):
        block = doc["providers"]
    codes = tuple(sorted(k for k in block if isinstance(k, str) and k.startswith("p_")))
    if not codes:
        raise RuntimeError("providers.yaml names no provider codes")
    return codes


class SkillAttribute(_Strict):
    """One attribute of the research vocabulary (``config/skill/attributes.yaml``): its canonical
    checklist row, its meaning and its signal tier (0 baseline, 1 to 3 signals)."""

    attribute: str
    row_key: str
    meaning: str
    tier: int = Field(ge=0, le=3)


class SkillVocabulary(_Strict):
    """What the client files read from the skill configuration: the attribute vocabulary, the
    news window (``config/skill/scope.yaml`` ``windows_months.news``) and a version (the hash of
    both files) for the title block. Loaded here, never through the agent layer (the export
    builders sit below it)."""

    attributes: dict[str, SkillAttribute]
    news_window_months: int = Field(gt=0)
    version: str

    def attribute(self, key: str) -> SkillAttribute | None:
        """The spec of a stored attribute key (``headquarters`` or ``row.headquarters``), or
        None for a key outside the vocabulary (kept under its row, never dropped)."""
        return self.attributes.get(key) or self.attributes.get(key.rsplit(".", 1)[-1])


@lru_cache(maxsize=1)
def skill_vocabulary(directory: Path | None = None) -> SkillVocabulary:
    """``attributes.yaml`` and ``scope.yaml`` validated once per process."""
    d = (directory or CONFIG_DIR) / "skill"
    attributes_raw, attributes_hash = _load(d / "attributes.yaml")
    scope_raw, scope_hash = _load(d / "scope.yaml")
    try:
        rows = attributes_raw["rows"]["value"]  # type: ignore[index]
        specs = {
            str(name): SkillAttribute(
                attribute=str(name),
                row_key=str(row),
                meaning=str(spec["meaning"]),
                tier=spec["tier"],
            )
            for row, attrs in rows.items()
            for name, spec in attrs.items()
        }
        months = int(scope_raw["windows_months"]["news"]["value"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"skill vocabulary is invalid: {error}") from error
    version = hashlib.sha256(f"{attributes_hash}{scope_hash}".encode()).hexdigest()
    return SkillVocabulary(attributes=specs, news_window_months=months, version=version)


def registrable(host: str) -> str:
    """``example.com`` of ``api.example.com`` (the last two labels; enough for the provider and
    aggregator hosts this layer compares)."""
    return ".".join(host.casefold().split(".")[-2:])


@lru_cache(maxsize=1)
def provider_hosts() -> tuple[str, ...]:
    """The registrable hosts of every data provider in ``config/providers.yaml`` (the data tools
    a client file must never name or link). Read once; never retyped in code."""
    doc = yaml.safe_load((CONFIG_DIR / "providers.yaml").read_text())
    block = doc
    if isinstance(doc, dict) and isinstance(doc.get("providers"), dict):
        block = doc["providers"]
    hosts: set[str] = set()
    for code, entry in block.items():
        if not (isinstance(code, str) and code.startswith("p_") and isinstance(entry, dict)):
            continue
        host = (urlsplit(str(entry.get("base_url") or "")).hostname or "").casefold()
        if host:
            hosts.add(registrable(host))
    if not hosts:
        raise RuntimeError("providers.yaml names no provider host")
    return tuple(sorted(hosts))
