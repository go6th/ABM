"""Read model for the Job Postings deliverable (spec 4, 8.4), scanned before it leaves (W7, J15).

Fields are an explicit allow-list: class, lane, platforms, platform sentences, the full
description (stored text with contact details removed), link, dates and status. Never: provider
codes, raw references, call references, credit figures or internal flags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from account_intelligence.safety.client_output import assert_client_safe
from jobs.models import Posting
from jobs.normalise import strip_pii


def is_exported(p: Posting) -> bool:
    """The Job Postings selection: read, non-hostile postings that name a platform in their
    description and fall inside the posting window (one definition, used by ``export_rows`` and
    the builders). A carried posting that has since slid past the window stays stored but is not
    listed, so the file's rows, the counts printed beside them (``jobs.counts`` leaves out the
    same postings) and the Tech ranking (which reads the window too) agree (review 2026-10-29,
    finding 4)."""
    return p.read and not p.hostile and bool(p.platforms) and p.window_bucket != "out_of_window"


def export_rows(
    postings: Sequence[Posting], texts: Mapping[str, str], extra_terms: Sequence[str] = ()
) -> list[dict[str, object]]:
    """Client-safe rows for read, classified, non-hostile postings; raises on any leak."""
    rows: list[dict[str, object]] = [
        {
            "class": p.posting_class,
            "lanes": list(p.lanes),
            "platforms": list(p.platforms),
            "platform_sentences": [s.text for s in p.sentences],
            "description": strip_pii(texts.get(p.posting_key, "")),
            "title": p.title,
            "url": p.url,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
            "status": p.status,
        }
        for p in postings
        if is_exported(p)
    ]
    assert_client_safe([str(rows)], extra_terms)
    return rows
