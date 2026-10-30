"""The AccountBundle models, a mirror of ``schema/account_bundle.schema.json``: every object
forbids unknown fields, every constraint of the schema (lengths, patterns, enumerations, row
widths) is a field constraint here, so the Pydantic validation and the JSON Schema accept and
reject the same documents (``tests/parity/test_bundle.py``). Strict mode: no coercion between
strings and numbers, as the schema has none. The ``date`` and ``uri`` formats are annotations
in the schema and plain strings here (a JSON Schema validator checks formats only when asked).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "account_bundle.schema.json"

Priority = Literal["A", "B", "C"]
Class = Literal["Build role", "Admin and owner role", "Working tool", "User role"]
Status = Literal["Open", "Closed"]
Row5 = Annotated[list[str], Field(min_length=5, max_length=5)]
Row6 = Annotated[list[str], Field(min_length=6, max_length=6)]
Row4 = Annotated[list[str], Field(min_length=4, max_length=4)]
Row2 = Annotated[list[str], Field(min_length=2, max_length=2)]
SourceId = Annotated[str, StringConstraints(pattern=r"^S[0-9]+$")]
JobId = Annotated[str, StringConstraints(pattern=r"^J[0-9]+$")]


class Strict(BaseModel):
    """Base of every bundle object: unknown fields are refused, nothing is coerced, and an
    optional field the schema types without ``null`` (``NOT_NULL``) may be absent but never
    null."""

    model_config = ConfigDict(extra="forbid", strict=True)
    NOT_NULL: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def _refuse_null(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None and info.field_name in cls.NOT_NULL:
            raise ValueError("null is not allowed here (the field may only be absent)")
        return value


class Window(Strict):
    """The 12 month window (ISO dates)."""

    from_: str = Field(alias="from")
    to: str


class WindowDisplay(Strict):
    """The 12 month window as printed."""

    from_: str = Field(alias="from")
    to: str


class Entity(Strict):
    """An entity label and whether its postings count as the account's."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["before", "note", "patterns", "phrase"])

    label: str
    counted_as_account: bool
    note: str | None = None
    phrase: str | None = None
    patterns: list[str] | None = None
    before: str | None = None


class SourceIds(Strict):
    """The source IDs of the data pulls, printed in headings and paragraphs."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(
        ["feed", "detections", "teams", "news_feed", "live_board"]
    )

    feed: str | None = None
    detections: str | None = None
    teams: str | None = None
    news_feed: str | None = None
    live_board: str | None = None


class Meta(Strict):
    """The account, the dates, the lanes and the entity labels."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(
        [
            "feed_unique_links",
            "live_capture_count",
            "out_of_scope_platforms",
            "people_lanes",
            "pursuing_client_aliases",
            "raw_board_counts",
            "source_ids",
            "part_c_lanes",
        ]
    )

    account_short: Annotated[
        str,
        StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9 .&'-]*$"),
    ]
    account_legal: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    domain: Annotated[str, StringConstraints(min_length=3, max_length=253)]
    careers_site: str | None = None
    run_date: str
    pull_date: str
    pull_date_short: str | None = None
    window: Window
    window_display: WindowDisplay
    pursuing_client_display: str
    pursuing_client_internal: str | None = None
    pursuing_client_aliases: list[str] | None = None
    byline: str
    lanes: Annotated[list[str], Field(min_length=1)]
    people_lanes: list[str] | None = None
    entities: Annotated[list[Entity], Field(min_length=1)]
    out_of_scope_platforms: list[str] | None = None
    live_capture_count: Annotated[int, Field(ge=0)] | None = None
    feed_unique_links: Annotated[int, Field(ge=0)] | None = None
    raw_board_counts: dict[str, int] | None = None
    source_ids: SourceIds | None = None
    part_c_lanes: list[str] | None = None


class Source(Strict):
    """One source of the register."""

    name: str
    url: str
    type: str
    date: str
    how: str
    supports: str


class JobRead(Strict):
    """One posting read in full (a J ID)."""

    name: str
    url: str
    plats: str
    cls: Class
    what: str
    status: Status
    f: str
    ent: str


class JobRow(Strict):
    """One row of the Jobs sheet."""

    requisition: str | None = None
    title: str
    platforms: list[str]
    cls: Class
    status: Status
    first_seen: str
    last_seen: str
    location: str
    seniority: str | None
    entity: str
    what: str
    platform_sentences: Annotated[str, StringConstraints(max_length=3000)]
    description: Annotated[str, StringConstraints(max_length=32000)]
    url: str


class PersonRow(Strict):
    """One row of the People sheet."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(
        ["employer_label", "former_si", "location", "note"]
    )

    name: str
    title: str
    level: Annotated[int, Field(ge=1, le=9)]
    senior: bool
    lane: str
    priority: Priority
    former_si: str | None = None
    found_by: str
    profile_url: str | None = None
    second_profile_url: str | None = None
    location: str | None = None
    note: str | None = None
    employer_label: str | None = None


class PeopleExcluded(Strict):
    """The rows removed before the People sheet."""

    domain: list[str]
    security: list[str]
    unresolved: list[str]
    below_rule_no_stack: list[str]


class Override(Strict):
    """An analyst correction applied after the rules."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["lane", "note", "priority"])

    name: str
    priority: Priority | None = None
    lane: str | None = None
    note: str | None = None


class StackRow(Strict):
    """One row of the Confirmed stack sheet."""

    business_function: str
    category: str
    technology: str
    postings: int | None
    lane: str


class TeamRow(Strict):
    """One row of the Platform teams sheet."""

    team: str
    lane_technologies: str
    postings: int | None
    first_activity: str
    last_activity: str
    technologies: str
    locations: str
    job_functions: str


class RankingRow(Strict):
    """One ranked platform."""

    platform: str
    category: str
    lane: str
    count: int
    open: int
    last_seen: str
    in_stack: Literal["Yes", "No", "Not pulled"]
    checked: Literal["Yes", "No"]


class SeenOnceRow(Strict):
    """A platform named once, not ranked."""

    platform: str
    category: str
    lane: str


class RankingBlock(Strict):
    """The Platform Ranking sheet's data."""

    base: Annotated[int, Field(ge=0)]
    rows: list[RankingRow]
    seen_once: list[SeenOnceRow]


class JobsCount(Strict):
    """Per lane platform counts."""

    total: int
    open: int
    build: int
    admin: int
    tool: int
    user: int


class Prio(Strict):
    """Priority counts among senior people."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["A", "B", "C"])

    A: int | None = None
    B: int | None = None
    C: int | None = None


class Computed(Strict):
    """Every number, computed by code."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["pf"])

    jobs_counts: dict[str, JobsCount]
    ent_counts: dict[str, int]
    lane_rows_account: int
    lane_rows_all: int
    people_total: int
    senior: int
    prio: Prio
    excluded: dict[str, int]
    pf: dict[str, Any] | None = None
    tracker_tally: dict[str, int]


class Narrative(Strict):
    """Section 1 of the Account Summary."""

    picture: list[str]
    stands_out: list[str]
    lanes: list[Row2]
    wrong: list[str]
    sharpen: list[str]


class Signals(Strict):
    """Section 4 tables."""

    tier_1: list[Row5] = Field(alias="Tier 1")
    tier_2: list[Row5] = Field(alias="Tier 2")
    tier_3: list[Row5] = Field(alias="Tier 3")
    context: list[Row5] = Field(alias="Context and history")


class PeopleText(Strict):
    """Section 6 paragraphs."""

    built: str
    profiles: str
    exclusions: str


class Extract(Strict):
    """A Part D extract."""

    id: str
    title: str
    url: str
    quotes: list[str]


def _not_bool(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number here")
    return value


Detection = Annotated[str | int | float, BeforeValidator(_not_bool)]
DetectionRow = Annotated[list[Detection], Field(min_length=6, max_length=6)]


class Evidence(Strict):
    """The Sources and Evidence parts that are not the register or the J IDs."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(
        [
            "board_paragraph",
            "detections_note",
            "news_events_heading",
            "part_b_intro",
            "part_c_closing",
            "part_g",
            "teams_note",
        ]
    )

    extracts: list[Extract]
    live_rows: list[Row6]
    live_lane_count: int
    leads: list[Row4]
    detections: list[DetectionRow]
    news_events: list[Row4]
    news_events_heading: str | None = None
    board_paragraph: str | None = None
    part_c_closing: str | None = None
    part_b_intro: str | None = None
    part_g: str | None = None
    detections_note: str | None = None
    teams_note: str | None = None


class StackNotes(Strict):
    """The Tech Stack and Teams workbook's titles and notes."""

    confirmed_title: str
    confirmed_note: str
    teams_title: str
    teams_note: str
    ranking_notes: list[str]


class WorkbookNotes(Strict):
    """Every workbook note."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(
        ["jobs_other_entities_label", "people_security_line_at", "people_security_line_template"]
    )

    people: list[str]
    jobs: list[str]
    stack: StackNotes
    people_security_line_template: str | None = None
    people_security_line_at: Annotated[int, Field(ge=0)] | None = None
    jobs_other_entities_label: str | None = None


class LaneSection(Strict):
    """One lane of the Lane Highlights file."""

    heading: str
    bullets: list[str]


class LaneHighlights(Strict):
    """The Lane Highlights file."""

    subtitle: str
    sections: list[LaneSection]


class InternalOnly(Strict):
    """The pursuing client's own records: never in the zip, never in a share."""

    title_suffix: str
    dates: str
    items: list[Row2]
    why: list[str]
    instead: str


class Prose(Strict):
    """Every sentence the files print."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["hiring_base"])

    glossary: str
    narrative: Narrative
    news: list[Row5]
    news_not_covered: str
    baseline: list[str]
    signals: Signals
    platform: list[Row4]
    hiring_interpretation: str
    hiring_base: str | None = None
    people_text: PeopleText
    key_people: list[Row6]
    profile_findings: list[Row4]
    status_changes: list[Row4]
    people_interpretation: list[str]
    hypotheses: list[Row2]
    cautions: list[str]
    first_call: list[str]
    waiting: list[str]
    tracker_p1: list[Row6]
    tracker_p2: list[Row6]
    evidence: Evidence
    workbook_notes: WorkbookNotes
    lane_highlights: LaneHighlights | None = None
    internal_only: InternalOnly | None = None


class AccountBundle(Strict):
    """One research run's whole content for one account."""

    NOT_NULL: ClassVar[frozenset[str]] = frozenset(["overrides"])

    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.[0-9]+\.[0-9]+$")]
    meta: Meta
    sources: dict[SourceId, Source]
    jobs_read: dict[JobId, JobRead]
    jobs_rows: list[JobRow]
    people_rows: list[PersonRow]
    people_excluded: PeopleExcluded
    overrides: list[Override] | None = None
    stack_rows: list[StackRow]
    team_rows: list[TeamRow]
    ranking: RankingBlock
    computed: Computed
    prose: Prose


def validate_bundle(data: Mapping[str, Any]) -> AccountBundle:
    """The bundle, or a ``pydantic.ValidationError`` naming every violation."""
    return AccountBundle.model_validate(dict(data))


def load_bundle(path: str | Path) -> AccountBundle:
    """Read a bundle from a JSON file (``.gz`` when gzip compressed) and validate it."""
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    return validate_bundle(data)


def dump_bundle(bundle: AccountBundle) -> dict[str, Any]:
    """The bundle as the JSON document the schema describes (aliases, no omitted fields set
    to None)."""
    return bundle.model_dump(by_alias=True, exclude_none=True)


def load_schema() -> dict[str, Any]:
    """The JSON Schema document."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data
