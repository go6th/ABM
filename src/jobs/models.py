"""Immutable job pipeline contracts and row-derived result models."""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassificationLabel(StrEnum):
    BUILD = "build"
    ADMIN_OWNER = "admin_owner"
    WORKING_TOOL = "working_tool"
    USER = "user"
    UNCLEAR = "unclear"


class RawPosting(BaseModel):
    """Connector-independent contract; provider payload shape remains [VERIFY]."""

    model_config = ConfigDict(frozen=True, extra="forbid")
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


class PlatformSentence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    platform: str
    text: str
    start: int
    end: int
    sha256: str


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    label: ClassificationLabel
    reason: str
    rule_id: str | None = None
    evidence_sentence: str | None = None


class Posting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    posting_key: str
    weak_key: bool = False
    title: str
    description: str | None
    url: str
    urls: tuple[str, ...]
    provider_code: str
    raw_ref: str
    retrieved_at: str
    posted_at: date | None = None
    first_seen_at: date | None = None
    status: Literal["open", "closed", "unknown"] = "unknown"
    status_source: str = "unknown"
    text_complete: bool | None = None
    full_text_read: bool = False
    window_bucket: Literal["in_window", "out_of_window", "undated"]
    platforms: tuple[str, ...] = ()
    sentences: tuple[PlatformSentence, ...] = ()
    classification: Classification | None = None
    hostile: bool = False
    lane: str | None = None

    @classmethod
    def from_raw(cls, raw: RawPosting, as_of: date) -> Posting:
        from datetime import date as parse_date

        from jobs.normalise import canonical_url, normalise_status, posting_key
        from jobs.window import classify_window

        def parsed(value: str | None) -> date | None:
            if not value:
                return None
            try:
                return parse_date.fromisoformat(value[:10])
            except ValueError:
                return None

        posted = parsed(raw.posted_at)
        first = parsed(raw.first_seen_at)
        key = str(posting_key(raw))
        weak = not bool(raw.source_id or canonical_url(raw.url))
        status = "open" if raw.live_board_present else normalise_status(raw.status)
        src = (
            "live_board"
            if raw.live_board_present
            else ("feed" if status != "unknown" else "unknown")
        )
        return cls(
            posting_key=key,
            weak_key=weak,
            title=raw.title,
            description=raw.description,
            url=canonical_url(raw.url),
            urls=tuple(
                sorted({canonical_url(raw.url), *(canonical_url(u) for u in raw.other_urls)})
            ),
            provider_code=raw.provider_code,
            raw_ref=raw.raw_ref,
            retrieved_at=raw.retrieved_at,
            posted_at=posted,
            first_seen_at=first,
            status=status,
            status_source=src,
            text_complete=raw.text_complete,
            full_text_read=raw.text_complete is True and bool(raw.description),
            window_bucket=classify_window(posted or first, as_of),
        )


class CountGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    base: str
    open: int
    closed: int
    unknown_status: int


class PostingCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pulled: int
    deduped: int
    in_window: int
    out_of_window: int
    undated: int
    matched: int
    read: int
    matched_status: CountGroup
    read_status: CountGroup
    by_class: dict[str, int]
    with_platform_sentence: int
    full_text_read: int
    full_text_missing: int
    title_only: int
    hostile: int
    per_platform: dict[str, int] = Field(default_factory=dict)


def digest_text(text: str) -> str:
    """Return a stable SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
