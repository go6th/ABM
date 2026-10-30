"""The contract of the repair stage: the workspace profile, the table result, the placeholders and
the schema of every table. Every builder takes a ``WorkspaceProfile`` and plain records
(``records.py``) and returns ``RepairedTable`` values; nothing here reads a database, a clock or a
file, so one function gives one answer for every workspace."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ---- placeholders (spec rule 2): the only words allowed in place of an empty cell -------------
NOT_FOUND = "Not found"
NOT_APPLICABLE = "Not applicable"
UNKNOWN = "Unknown"
OUTSIDE_WINDOW = "Outside window"
NOT_ACCOUNT = "Not the account"


def not_collected(reason: str) -> str:
    """``Not collected: <reason>`` (the reason is required; a blank one is a defect)."""
    reason = reason.strip()
    if not reason:
        raise ValueError("a Not collected placeholder needs a reason")
    return f"Not collected: {reason}"


_PLACEHOLDER = re.compile(
    r"^(Not found|Not applicable|Unknown|Outside window|Not the account"
    r"|Not collected: \S.*|Closed \(not found on site, .+\))$"
)


def is_placeholder(value: str) -> bool:
    """True when ``value`` is one of the allowed explicit markers."""
    return bool(_PLACEHOLDER.match(value.strip()))


# ---- the schema of every table: the columns a repaired table must have, in order ---------------
JOBS_COLUMNS = (
    "Requisition ID", "Posting ID", "Title", "Entity", "Platforms named", "Lane(s)", "Class",
    "Status", "First seen", "Last seen", "Posted", "Location", "Seniority",
    "Platform sentences", "Full description", "Source URL", "Collected",
)  # fmt: skip
JOBS_ALL_COLUMNS = (
    "Requisition ID", "Posting ID", "Title", "Entity", "Status", "First seen", "Last seen",
    "Posted", "Location", "Seniority", "Platforms named", "Source URL", "Collected",
)  # fmt: skip
PEOPLE_COLUMNS = (
    "Name", "Title as reported", "Level", "Meets seniority rule", "Lane", "Priority (A/B/C)",
    "Former SI and consulting employers", "Found by", "LinkedIn", "Location", "Entity",
    "Last verified", "Note",
)  # fmt: skip
LEADERSHIP_COLUMNS = (
    "Name", "Role", "Start date", "End or retire date", "Status", "Source URL", "Source type",
    "Note",
)  # fmt: skip
RANKING_COLUMNS = (
    "Rank", "Platform", "Category", "Lane", "Postings naming it", "Share of base", "Open now",
    "In confirmed stack",
)  # fmt: skip
STACK_COLUMNS = (
    "Business function", "Category", "Technology", "Postings naming it", "Detections",
    "Cross check", "First seen", "Last seen", "Source URL", "Source type", "Detected domain",
)  # fmt: skip
TEAM_COLUMNS = ("Team", "Function", "Location", "Postings", "Platforms")
NEWS_COLUMNS = (
    "Article ID", "Headline", "Published", "Collected", "Within window (Y/N)", "Superseded by",
    "Fact grade", "Publisher", "Source type", "Source URL",
)  # fmt: skip
NEWS_FACT_COLUMNS = ("Article ID", "Fact", "Fact grade", "Quote")
SOURCES_COLUMNS = (
    "Code", "Title", "Publisher", "Published", "Source type", "Link",
    "Used in (deliverable + section)", "Collected",
)  # fmt: skip
SEARCH_COLUMNS = (
    "Query", "Purpose (Coverage item #)", "Providers", "First run", "Runs",
    "Pages returned (total)", "Sites", "Used (Y/N)",
)  # fmt: skip
SIGNAL_COLUMNS = (
    "Signal", "Type", "Lane", "Fact grade", "Detected", "Event date", "Evidence (source codes)",
)  # fmt: skip
COVERAGE_COLUMNS = (
    "#", "Research item", "Priority", "Status", "Reason", "Evidence (row counts / source codes)",
    "Last run",
)  # fmt: skip
MAIL_COLUMNS = ("Title", "Kind", "From", "Date", "Summary", "Relevance", "Link")

SIGNAL_TYPES = (
    "Leadership change", "Platform hiring spike", "Build-role hiring", "M&A / divestiture",
    "Regulatory event", "Funding / financial", "Program or migration",
)  # fmt: skip
CLASSES = ("Build role", "Admin and owner role", "Working tool", "User role")
FACT_GRADES = ("Confirmed", "Probable", "Lead")
PRIORITIES = ("A", "B", "C")
LEADERSHIP_STATUSES = ("Current", "Departed", "Moved to divested business", "Unverified")


@dataclass(frozen=True)
class EntityRule:
    """One entity an account's rows can belong to. ``patterns`` are regexes searched ignoring
    case in a posting's title/description or a person's employer text; ``counts_as_account`` says
    whether the rows count in the account's numbers (a subsidiary: yes; a divested business: no).
    ``before`` is an ISO date: rows first seen before it belong to the pre-close business."""

    label: str
    patterns: tuple[str, ...] = ()
    counts_as_account: bool = False
    before: str | None = None


@dataclass(frozen=True)
class WorkspaceProfile:
    """Everything one workspace needs, as data (built by ``profile.py`` from the account, its
    entity lock, the skill scope and the consents; never typed per account)."""

    account_name: str
    account_short: str
    domain: str
    run_date: date
    window_from: date
    window_to: date
    # registrable domains that belong to the account (primary, redirects, subsidiaries, boards)
    domains: frozenset[str] = frozenset()
    # names and brands the account's own text uses ("BD", "Becton Dickinson"); lower-cased match
    aliases: tuple[str, ...] = ()
    # the entity every account row carries; the others come from ``entities``
    account_label: str = ""
    entities: tuple[EntityRule, ...] = ()
    lanes: tuple[str, ...] = ()
    # case-insensitive whole-word title phrases never listed as people
    title_exclusions: tuple[str, ...] = ()
    # platform names never searched, listed or counted for this account
    out_of_scope_platforms: tuple[str, ...] = ()
    people_consent: bool = False
    mail_drive_connected: bool = False
    # (host suffix, publisher) pairs; the generic map is added by ``sources.PUBLISHERS``
    publishers: tuple[tuple[str, str], ...] = ()
    # a pulled posting host that is the account's own careers board (for the Source type)
    careers_hosts: frozenset[str] = frozenset()
    # the platform dictionary (parity.platforms.Platforms); typed Any to keep this module pure
    platforms: Any = None

    @property
    def label(self) -> str:
        """The Entity value of an account row."""
        return self.account_label or self.account_short or self.account_name


@dataclass(frozen=True)
class Exclusion:
    """One dropped or excluded row, for ``Logs/exclusions.csv``."""

    table: str
    key: str
    reason: str
    detail: str = ""


@dataclass
class Sheet:
    """One worksheet / CSV file: a header and string-cell rows (cells keyed by column)."""

    name: str
    columns: tuple[str, ...]
    rows: list[dict[str, str]] = field(default_factory=list)
    # column -> hyperlink target for XLSX (optional; the cell text stays the visible value)
    private: bool = False

    def cell_rows(self) -> list[list[str]]:
        """The rows as lists in column order (a missing key is an empty cell, which the checks
        report)."""
        return [[str(r.get(c, "")) for c in self.columns] for r in self.rows]


@dataclass
class RepairedTable:
    """One table of the export: its sheets (the first is the main one), what was dropped, and the
    numbers the report and the deliverable cross-check use."""

    key: str  # jobs, people, tech, news, sources, searches, signals, coverage, mail
    title: str  # the file stem in Tables/
    sheets: list[Sheet]
    exclusions: list[Exclusion] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # one-line honest statements (blocked, etc.)
    internal: bool = False  # goes to "Internal - your files/", not Tables/

    @property
    def main(self) -> Sheet:
        """The first sheet."""
        return self.sheets[0]


def stat_int(stats: Mapping[str, Any], key: str) -> int:
    """An integer statistic (0 when absent)."""
    value = stats.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0


def span(start: date | None, end: date | None, day: date) -> bool:
    """Whether ``day`` lies in [start, end] (an open end counts as inside)."""
    return (start is None or day >= start) and (end is None or day <= end)


def iso(day: date | None, default: str = UNKNOWN) -> str:
    """An ISO date cell, or the explicit marker for a date that is not known."""
    return day.isoformat() if day else default


def first(values: Sequence[Any], default: Any = None) -> Any:
    """The first truthy value."""
    return next((v for v in values if v), default)
