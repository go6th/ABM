"""Immutable job pipeline contracts. Counts are never typed: ``PostingCounts`` is built only by
``jobs.counts.compute_counts`` from posting rows.

``RawPosting`` is the contract the connector adapter fills from the normalised connector record
(``JobOpeningRecord`` for the feed, ``WebPageRecord`` for live board hits, ``ScrapedPageRecord``
for an escalation read). The provider response shape behind those records is the connector
layer's sourced contract; nothing here guesses it.
"""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PostingClass = Literal["build", "admin_owner", "working_tool", "user", "unclassified"]
PostingStatus = Literal["open", "closed", "unknown"]
StatusSource = Literal["live_board", "feed", "unknown"]
WindowBucket = Literal["in_window", "out_of_window", "undated"]
# J5 escalation of an unread matched posting: never tried, read, failed (the one read is spent),
# or skipped because the run-level page-read cap was reached (a gap pass may still read it)
Escalation = Literal["none", "read", "failed", "cap"]


class ClassificationLabel(StrEnum):
    """What the extractor may answer (the four classes or ``unclear``)."""

    BUILD = "build"
    ADMIN_OWNER = "admin_owner"
    WORKING_TOOL = "working_tool"
    USER = "user"
    UNCLEAR = "unclear"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RawPosting(_Frozen):
    """One posting as a source returned it, with provenance (J10). Text is untrusted."""

    source_id: str | None = None
    title: str
    description: str | None = None
    url: str
    other_urls: tuple[str, ...] = ()
    provider_code: str
    raw_ref: str
    retrieved_at: str
    posted_at: str | None = None
    first_seen_at: str | None = None
    status: str | None = None
    text_complete: bool | None = None
    live_board_present: bool = False
    location: str | None = None
    call_ref: str | None = None
    # the feed's own last-seen timestamp and seniority word (the reference workbook prints both)
    last_seen_at: str | None = None
    seniority: str | None = None


class SourceBatch(_Frozen):
    """What one logical source step returned: postings, the ledger keys behind them, and why
    it stopped. ``failed_refs`` are calls that failed (tracker proof of a failure)."""

    postings: tuple[RawPosting, ...] = ()
    call_refs: tuple[str, ...] = ()
    failed_refs: tuple[str, ...] = ()
    complete: bool = True
    exit_reason: str = "exhausted"
    reported_count: int | None = None
    # records of this one call dropped as another company's (the entity lock's look-alike hosts)
    dropped: int = 0


class PageText(_Frozen):
    """An escalation read of one posting URL."""

    text: str
    complete: bool
    call_ref: str


class PlatformSentence(_Frozen):
    """A verbatim span of the stored posting text naming a platform (J3).

    ``start``/``end`` index the stored text; ``sha256`` hashes that verbatim span; ``text`` is the
    span with email addresses and phone numbers removed (derived field, PII proposal)."""

    platform: str
    text: str
    start: int = Field(ge=0)
    end: int
    sha256: str

    @model_validator(mode="after")
    def _ordered(self) -> PlatformSentence:
        if self.end <= self.start:
            raise ValueError("sentence end must follow start")
        return self


class Classification(_Frozen):
    """How a posting's class was decided (J4). Never from the title alone."""

    label: PostingClass
    # ``rule``: the title/keyword rules of earlier runs (kept so stored rows load); ``heuristic``:
    # the reference's Working tool / User role rule over the platform sentences (jobs.heuristic)
    classified_by: Literal["rule", "extractor", "heuristic", "none"]
    reason: str
    rule_id: str | None = None
    evidence_sentence: str | None = None
    second_pass_label: str | None = None


class Posting(_Frozen):
    """One deduplicated posting of this run, with everything the counts are computed from."""

    posting_key: str
    weak_key: bool = False
    title: str
    location: str | None = None
    url: str
    urls: tuple[str, ...]
    provider_code: str
    raw_ref: str
    retrieved_at: str
    posted_at: date | None = None
    first_seen_at: date | None = None
    status: PostingStatus = "unknown"
    status_source: StatusSource = "unknown"
    text_ref: str | None = None
    text_sha256: str | None = None
    text_complete: bool | None = None
    read: bool = False
    full_text_read: bool = False
    full_text_missing: bool = False
    escalation: Escalation = "none"
    window_bucket: WindowBucket
    platforms: tuple[str, ...] = ()
    title_platforms: tuple[str, ...] = ()
    lanes: tuple[str, ...] = ()
    sentences: tuple[PlatformSentence, ...] = ()
    classification: Classification | None = None
    hostile: bool = False
    call_refs: tuple[str, ...] = ()
    # the feed's last-seen date and seniority word as given (None when the source gave none)
    last_seen: date | None = None
    seniority: str | None = None
    # incremental re-research: True when first seen after the account's previous delivered
    # research, False when carried over from it, None when the run had no previous research
    new_since_previous: bool | None = None
    # the day the posting was first captured by a research (its retrieval date when it first
    # became a row). A posting only the live board holds has no feed date; this is the First seen
    # the file prints for it, and a carried posting keeps it (review 2026-10-29, finding 14)
    captured_on: date | None = None
    # the research date on which the status last changed (a carried posting whose status moved:
    # open to closed, or closed to open again). It is part of the archive copy's identity so a
    # posting that returns to an earlier state is a new copy, not the old one (finding 13)
    status_since: date | None = None

    @property
    def matched(self) -> bool:
        """Names a platform in its description or title (title-only is a Lead candidate)."""
        return bool(self.platforms or self.title_platforms)

    @property
    def title_only(self) -> bool:
        """A title match with no description match: never classified (D-16)."""
        return bool(self.title_platforms) and not self.platforms

    @property
    def posting_class(self) -> PostingClass:
        """The class used in counts; anything not decided is ``unclassified``."""
        return self.classification.label if self.classification else "unclassified"


class PreviousPostings(_Frozen):
    """The account's previous delivered research, as the incremental pass carries it: its run,
    its ``as_of`` date and every posting it stored (text references, sentences, classes, status
    and first/last seen included), so none of them is pulled, read or classified again."""

    run_id: str
    as_of: date
    postings: tuple[Posting, ...] = ()


class StatusCounts(_Frozen):
    """Open, closed and unknown over a named base; the three always sum to the base."""

    base: Literal["matched", "read"]
    total: int
    open: int
    closed: int
    unknown_status: int


class CountBlock(_Frozen):
    """All counts for one platform (or for the distinct set)."""

    matched: int
    read: int
    matched_status: StatusCounts
    read_status: StatusCounts
    by_class: dict[str, int]
    with_platform_sentence: int
    full_text_read: int
    full_text_missing: int
    title_only: int


class NotSearched(_Frozen):
    """A platform or lane that was not searched: shown with its reason, never as 0 (J9)."""

    reason: str


class PostingCounts(_Frozen):
    """The count chain, the distinct block and the per-platform blocks."""

    pulled: int
    deduped: int
    in_window: int
    out_of_window: int
    undated: int
    hostile: int
    distinct: CountBlock
    per_platform: dict[str, CountBlock | NotSearched] = Field(default_factory=dict)


def digest_text(text: str) -> str:
    """Stable SHA-256 of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
