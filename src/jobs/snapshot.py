"""The structured copy of a job posting kept in the link archive (``posting-archive/1``).

A careers site removes a posting when its requisition closes and the old link answers 404. The
research stored the posting's text; this module turns the posting the pipeline holds (its fields,
its platform sentences, its class and its stored text) into ONE JSON record the website can show
like a web archive page: the posting as it read when it was captured, with the fields a seller
looks for (requisition, location, status when captured, first and last seen, seniority, class,
platforms named, the sentences that name them) and the full description.

Pure functions only: the repository (``jobs.repo``) reads the stored text, calls ``build_snapshot``
and stores the bytes. The requisition reading is the one the Job Postings workbook uses
(``requisition_of``; ``exports.readers.jobs`` imports it from here, so the file and the archive
page can never disagree on a requisition). The pattern and the class words are read from the
same configuration file the workbook reads (``config/export_rules.yaml``), not copied.

Client-visible, so nothing in the record names a data tool, a provider or a credit figure: every
free-text field passes a guard (``Guard``: the text to show and whether it must be withheld) and a
field that fails is withheld (blank, listed under ``withheld``), never the whole copy.

Identity: a copy is content-addressed over the fields that say what the posting SAID
(``identity_bytes``: not the capture time, not first or last seen), and the day its status last
changed. A re-research that sees the same posting unchanged writes nothing new; one that sees
changed content (the posting closed, a new location, a reworded description) writes a new copy
beside the old one, and one that sees the posting return to an earlier state (closed, then open
again) writes a new copy too, so the newest copy of an address always tells the latest state (the
archive is insert-only, the newest copy of an address wins, older copies stay).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from account_intelligence import archive_records as AR
from jobs.config import export_rule_texts
from jobs.models import Posting
from jobs.normalise import strip_pii

SCHEMA = "posting-archive/1"
SOURCE = AR.SOURCE
KIND = "posting"
# the free-text fields the guard checks (and may withhold); the rest are dates, words from fixed
# vocabularies and the address
_GUARDED = ("title", "requisition", "location", "seniority", "class", "description")

Guard = AR.Guard
default_guard = AR.default_guard
snapshot_bytes = AR.snapshot_bytes


def identity_bytes(record: Mapping[str, Any], status_since: date | None = None) -> bytes:
    """What the page said, without when we looked (``archive_records.identity_bytes``), plus the
    day its status last changed when it has changed since the posting was first captured. A
    posting that closes and is later open again says the same words as its first copy; without
    the day it would map to the first copy's key, nothing new would be written and the newest copy
    would still say ``closed``. With it, each change of status is a new copy, an unchanged status
    is the same copy every research, and a posting that never changed keeps the key it had."""
    if status_since is None:
        return AR.identity_bytes(record)
    return AR.identity_bytes({**record, "status_since": status_since.isoformat()})


def requisition_of(text: str, pattern: str) -> str:
    """The job ID the posting text prints after a job ID label (``workbooks.jobs
    .requisition_pattern``), else blank: never made up from the link or the feed's own key."""
    found = re.search(pattern, text or "")
    return found.group(1) if found else ""


def requisition_pattern() -> str:
    """The workbook's requisition pattern (one place: ``export_rules.yaml``)."""
    return str(export_rule_texts()["pattern"])


def class_label(class_id: str | None) -> str:
    """The class as the files print it (``Build role``), else the id."""
    labels: Mapping[str, str] = export_rule_texts()["classes"]
    return labels.get(class_id or "unclassified", class_id or "")


def build_snapshot(
    p: Posting,
    text: str,
    *,
    captured_at: datetime,
    guard: Guard = default_guard,
    pattern: str | None = None,
) -> dict[str, Any]:
    """The record for one posting and its stored text. A free-text field the guard refuses is
    blank and named in ``withheld``; contact details are removed from the description."""
    clean_text = strip_pii(text or "")
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "url": p.url,
        "title": p.title,
        "requisition": requisition_of(clean_text, pattern or requisition_pattern()),
        "location": p.location or "",
        "status_at_capture": p.status if p.status in {"open", "closed"} else "unknown",
        "first_seen": AR.day_of(p.first_seen_at),
        "last_seen": AR.day_of(p.last_seen),
        "posted_at": AR.day_of(p.posted_at),
        "seniority": p.seniority or "",
        "platforms": list(p.platforms),
        "class": class_label(p.posting_class),
        "platform_sentences": [{"platform": s.platform, "text": s.text} for s in p.sentences],
        "description": clean_text,
        "captured_at": captured_at.isoformat(),
        "source": SOURCE,
    }
    sentences = record.pop("platform_sentences")
    AR.guarded(record, _GUARDED, guard, lists=("platforms",))
    kept: list[dict[str, str]] = []
    refused_any = False
    for pair in sentences:
        shown, refused = guard(pair["text"])
        if refused:
            refused_any = True
        else:
            kept.append({"platform": pair["platform"], "text": shown})
    record["platform_sentences"] = kept
    if refused_any:
        record["withheld"] = sorted({*record.get("withheld", []), "platform_sentences"})
    return record
