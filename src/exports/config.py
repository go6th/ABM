"""Typed configuration for the export builders: the theme and the content rules.

Both files are validated when loaded (fail fast at startup) and hashed; the hashes are recorded on
every deliverable so a build is always traceable to the exact look and rules it used. This module
is the only place a style value is read; no renderer holds a style literal (static test).
GUARANTEE(test_every_config_entry_names_its_source_or_status)
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from account_intelligence.config import CONFIG_DIR
from exports.errors import ConfigError

ColumnKind = Literal["ref", "code", "label", "text_short", "text_long", "url", "date", "count"]


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


TableName = Literal["lane", "key_people", "linkedin", "tracker", "sources", "job_reads", "news"]


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
    """Account Summary section headings (PRD order)."""

    narrative: str
    caveats: str
    lane_tables: str
    key_people: str
    linkedin_findings: str
    executive_voice: str
    hypotheses: str
    first_call_questions: str
    decision_changes: str
    coverage_tracker: str
    unassigned_lane: str
    absence: str


class EvidenceHeadings(_Strict):
    """Sources and Evidence section headings."""

    sources: str
    job_descriptions: str
    news: str


class Headings(_Strict):
    """Headings per document."""

    source: str
    summary: SummaryHeadings
    evidence: EvidenceHeadings


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
    """Display words for stored vocabularies."""

    posting_class: dict[str, str]
    posting_status: dict[str, str]
    source_kind: dict[str, str]
    detection_grade: str
    source: str

    @model_validator(mode="after")
    def _vocabularies(self) -> Labels:
        classes = {"build", "admin_owner", "working_tool", "user", "unclassified"}
        if set(self.posting_class) != classes:
            raise ValueError("posting_class labels must cover the job pipeline's five classes")
        if set(self.posting_status) != {"open", "closed", "unknown"}:
            raise ValueError("posting_status labels must cover open, closed and unknown")
        if set(self.source_kind) != {"primary", "secondary", "aggregator"}:
            raise ValueError("source_kind labels must cover the sources.kind vocabulary")
        return self


class EvidenceRules(_Strict):
    """Evidence file selection rules."""

    news_attribute_prefixes: tuple[str, ...]
    news_source: str


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


class TechBook(_Strict):
    """Tech Stack and Teams workbook layout."""

    sheet: str
    columns: tuple[Column, ...] = Field(min_length=1)
    teams_sheet: str
    counts_sheet: str


class Workbooks(_Strict):
    """All workbook layouts."""

    source: str
    people: PeopleBook
    jobs: JobsBook
    tech: TechBook

    @model_validator(mode="after")
    def _ref_first(self) -> Workbooks:
        for book in (self.people, self.jobs, self.tech):
            keys = [c.key for c in book.columns]
            if keys[0] != "ref" or len(set(keys)) != len(keys):
                raise ValueError(f"{book.sheet}: the first column is ref and keys are unique")
        return self


class RawPayloads(_Strict):
    """Raw broker payloads in the zip (D-25: excluded until D-10 is answered)."""

    default: Literal["excluded"]
    neutral_name_copy_enabled: bool


class WorkspaceRules(_Strict):
    """Workspace zip contents and caps (D-25)."""

    decision: str
    status: str
    restore_text_source: str
    restore_text: str = Field(min_length=1)
    notes_text: str = Field(min_length=1)
    raw_payloads: RawPayloads
    max_members: int = Field(gt=0, le=10_000)
    max_member_bytes: int = Field(gt=0)
    max_total_bytes: int = Field(gt=0)
    entry_name_max: int = Field(gt=8, le=255)


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
    evidence: EvidenceRules
    workbooks: Workbooks
    workspace: WorkspaceRules


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
        """The configured label of a lane id, or None when the id is unknown."""
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


def load_export_config(directory: Path | None = None) -> ExportConfig:
    """Validate both files; any invalid or missing value raises ``ConfigError`` (no default is
    invented for a bad value)."""
    d = directory or CONFIG_DIR
    theme_data, theme_hash = _load(d / "export_theme.yaml")
    rules_data, rules_digest = _load(d / "export_rules.yaml")
    lanes_data, lanes_digest = _load(d / "lanes.yaml")
    spec_data, spec_digest = _load(d / "output_spec.yaml")
    rules_hash = hashlib.sha256(f"{rules_digest}{lanes_digest}{spec_digest}".encode()).hexdigest()
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
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"export configuration is invalid: {error}") from error


@lru_cache(maxsize=1)
def default_export_config() -> ExportConfig:
    """The repository configuration, loaded once per process."""
    return load_export_config()
