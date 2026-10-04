"""Immutable people pipeline contracts (allow-list models, ``extra='forbid'``).

``RawPerson``, ``RawProfile`` and ``RawPost`` are the contract the connector adapters fill from
normalised connector records. The provider response shapes behind them are NOT known for the
title search, the profile pull or the posts pull ([VERIFY] V-PEOPLE-1 to 3: one recorded sample per
operation is needed); nothing here guesses them. Anything a provider returns outside the allow-list
is dropped at the boundary (``people.profiles.from_fields``) and counted, so personal contact data
cannot reach a column, a claim, a log or an export (P9).

``PeopleCounts`` is built only by ``people.counts.compute_counts`` from rows; no caller types a
count.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ScopeStatus = Literal["in_scope", "out_of_scope", "excluded", "review", "unresolved", "merged"]
Origin = Literal["title_search", "association", "mention"]
Precision = Literal["year", "month", "day"]
ProfileState = Literal["not_requested", "read", "partial", "failed", "cap", "reused"]
STATUSES: tuple[ScopeStatus, ...] = (
    "in_scope",
    "out_of_scope",
    "excluded",
    "review",
    "unresolved",
    "merged",
)

_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PartialDate(_Frozen):
    """A date with the precision the source gave (year only, month, or day)."""

    year: int = Field(ge=1, le=9999)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def _shape(self) -> PartialDate:
        if self.day is not None and self.month is None:
            raise ValueError("a day needs a month")
        if self.day is not None:
            date(self.year, self.month or 1, self.day)  # raises on an impossible day
        return self

    @property
    def precision(self) -> Precision:
        """``year``, ``month`` or ``day``."""
        if self.day is not None:
            return "day"
        return "month" if self.month is not None else "year"

    def earliest(self) -> date:
        """The first calendar day the value can mean."""
        return date(self.year, self.month or 1, self.day or 1)

    def latest(self) -> date:
        """The last calendar day the value can mean."""
        if self.day is not None:
            return date(self.year, self.month or 1, self.day)
        if self.month is not None:
            nxt = date(self.year + (self.month == 12), self.month % 12 + 1, 1)
            return date.fromordinal(nxt.toordinal() - 1)
        return date(self.year, 12, 31)

    def iso(self) -> str:
        """``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``."""
        if self.day is not None:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.month is not None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"

    @classmethod
    def parse(cls, value: object) -> PartialDate | None:
        """Parse ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` (a longer ISO string is cut to the
        date). Anything else is ``None`` (unknown), never a guess."""
        if not isinstance(value, str):
            return None
        match = _DATE.fullmatch(value.strip()[:10])
        if match is None:
            return None
        year, month, day = (int(g) if g else None for g in match.groups())
        try:
            return cls(year=year or 0, month=month, day=day)
        except ValueError:
            return None


class RawPerson(_Frozen):
    """One person as a source returned it, with provenance (P1). Text fields are untrusted.

    ``source_record_id`` is stable for the record (``raw_ref`` and its index), so a replayed page
    yields the same id. ``search_domain`` is the domain the search was restricted to (evidence for
    the current-employer rule); ``is_current`` and ``employer_domain`` are as returned."""

    source_record_id: str = Field(min_length=1, max_length=300)
    origin: Origin
    provider_code: str | None = None
    raw_ref: str | None = None
    call_ref: str | None = None
    retrieved_at: str | None = None
    name: str | None = None
    title: str | None = None
    headline: str | None = None
    profile_url: str | None = None
    employer_domain: str | None = None
    is_current: bool | None = None
    location: str | None = None
    search_domain: str | None = None
    lane: str | None = None
    pass_no: int | None = None
    query: str | None = None
    mention_url: str | None = None
    mention_sentence: str | None = None
    fields_dropped: int = Field(default=0, ge=0)


class PersonPage(_Frozen):
    """One page of a people search: records, the cursor to continue from, and the ledger key."""

    records: tuple[RawPerson, ...] = ()
    next_cursor: str | None = None
    call_ref: str | None = None
    complete: bool = True
    reported_count: int | None = None


class RawProfile(_Frozen):
    """A full-profile response after the allow-list boundary (P9)."""

    profile_url: str
    provider_code: str
    raw_ref: str
    call_ref: str
    fields: dict[str, object] = Field(default_factory=dict)
    fields_dropped: int = Field(default=0, ge=0)
    complete: bool = True


class RawPost(_Frozen):
    """One post or comment as returned (text untrusted)."""

    post_url: str
    posted_at: str | None = None
    text: str = ""
    raw_ref: str
    call_ref: str


class PostsBatch(_Frozen):
    """Posts of one person and the ledger key of the call."""

    posts: tuple[RawPost, ...] = ()
    call_ref: str
    last_activity_at: str | None = None
    activity_known: bool = True


class Mention(_Frozen):
    """A person named in research by an earlier stage: the name, the URL and the sentence."""

    name: str = Field(min_length=1, max_length=200)
    mention_url: str
    sentence: str = Field(min_length=1, max_length=2000)


class Classification(_Frozen):
    """One status per person, with the rule and the matching substring as evidence (P4)."""

    status: ScopeStatus
    reason: str | None = None
    rule_id: str | None = None
    evidence: str | None = None
    level: str | None = None
    level_rule: str | None = None
    band: str | None = None
    lanes: tuple[str, ...] = ()
    priority: Literal["A", "B", "C"] | None = None
    priority_rule: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Classification:
        needs_reason = self.status in ("out_of_scope", "excluded", "review")
        if needs_reason and not self.reason:
            raise ValueError(f"{self.status} needs a reason")
        if self.status == "in_scope" and (self.priority is None or self.level is None):
            raise ValueError("in_scope needs a level and a priority")
        if self.status != "in_scope" and self.priority is not None:
            raise ValueError("only in_scope people have a priority")
        return self


class Person(_Frozen):
    """One found person (a source record or a mention) after merge and scope."""

    person_key: str
    source_record_id: str
    origin: Origin
    name: str | None = None
    title: str | None = None
    headline: str | None = None
    profile_url: str | None = None  # canonical; None when unresolved
    profile_url_raw: str | None = None
    employer_domain: str | None = None
    location: str | None = None
    provider_code: str | None = None
    raw_ref: str | None = None
    call_ref: str | None = None
    lane_searched: str | None = None
    pass_no: int | None = None
    query: str | None = None
    mention_url: str | None = None
    mention_sentence: str | None = None
    merged_into: str | None = None
    possible_duplicate: bool = False
    internal_only: bool = False
    classification: Classification
    profile_state: ProfileState = "not_requested"
    profile_failure: str | None = None
    posts_requested: bool = False
    activity_unfiltered: bool = False
    fields_dropped: int = 0

    @property
    def status(self) -> ScopeStatus:
        """The one status this person ends in (P5)."""
        return self.classification.status


class Profile(_Frozen):
    """Allow-listed profile fields (D-23). ``certifications`` ``None`` means the source did not
    provide the field; ``()`` means it provided an empty list (P6: not the same)."""

    person_key: str
    profile_url: str
    name: str | None = None
    current_title: str | None = None
    headline: str | None = None
    employer: str | None = None
    prior_employers: tuple[str, ...] | None = None
    join_date: PartialDate | None = None
    role_start: PartialDate | None = None
    certifications: tuple[str, ...] | None = None
    location: str | None = None
    own_description: str | None = None
    own_description_sha256: str | None = None
    last_activity_at: date | None = None
    provider_code: str
    raw_ref: str
    call_ref: str
    fields_dropped: int = 0
    internal_only: bool = False
    partial: bool = False
    hostile: bool = False


class Post(_Frozen):
    """One stored post: URL, date and a length-capped verbatim excerpt."""

    person_key: str
    post_url: str
    posted_at: date | None = None
    excerpt: str
    excerpt_sha256: str
    raw_ref: str
    call_ref: str
    internal_only: bool = False
    hostile: bool = False


class Finding(_Frozen):
    """A Findings from LinkedIn candidate that passed validation: a claim plus one verbatim
    sentence from that person's own text (P13)."""

    person_key: str
    claim: str
    sentence: str
    url: str


class AlumniCount(_Frozen):
    """Alumni over profiles read, or not computed with its reason (never 0 for unknown)."""

    computed: bool
    count: int | None = None
    base: int | None = None
    not_computed_reason: str | None = None


class PeopleCounts(_Frozen):
    """All counts, each with its base. Built from rows only (``people.counts``)."""

    found: int
    distinct: int
    merged_away: int
    possible_duplicate: int
    in_scope: int
    out_of_scope: dict[str, int]
    excluded: dict[str, int]
    review: dict[str, int]
    unresolved: int
    profiles_requested: int
    profiles_read: int
    profiles_partial: int
    profiles_failed: int
    alumni: AlumniCount
    tenure_known: int
    tenure_unknown: int
    date_invalid: int
    tenure_buckets: dict[str, int]
    cert_field_provided: int
    cert_field_absent: int
    certified_by_lane: dict[str, int]
    posts_people: int
    posts_pulled: int
    activity_unfiltered: int
    hostile: int
    fields_dropped: int
    by_lane_in_scope: dict[str, int]
    by_priority: dict[str, int]
