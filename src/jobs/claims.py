"""Claims templated by code from structured posting fields (J11).

One sourced claim per classified posting and named platform: attribute ``jobs.<platform>.<class>``,
value the platform name, evidence the posting URL and the VERBATIM platform sentence (the grading
engine re-checks it against the stored source, G4). The pipeline never sets a grade, never calls
a keyword match a finding (title-only, unread and heuristic-class postings get no claim), and
skips a sentence that contains contact details (PII proposal) rather than sending it.
"""

from __future__ import annotations

import hashlib
import re

from account_intelligence.grading.schema import ProposedEvidence, ProposedSourced
from jobs.models import Posting
from jobs.normalise import strip_pii


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_") or "platform"


def claims_for(posting: Posting, text: str) -> tuple[list[ProposedSourced], int]:
    """Claims for one posting from its stored ``text``; returns ``(claims, skipped_for_pii)``."""
    if posting.classification is None or posting.posting_class == "unclassified":
        return [], 0
    if posting.classification.classified_by == "heuristic":
        return [], 0  # a keyword rule over the sentences is not a reading: never a finding
    if not posting.read or posting.title_only or posting.hostile:
        return [], 0
    claims: list[ProposedSourced] = []
    skipped = 0
    for platform in posting.platforms:
        sentence = next((s for s in posting.sentences if s.platform == platform), None)
        if sentence is None:
            continue
        verbatim = text[sentence.start : sentence.end]
        if not verbatim:
            continue  # the stored text is not at hand (a carried copy that failed to read back)
        if strip_pii(verbatim) != verbatim:
            skipped += 1
            continue
        digest = hashlib.sha256(f"{posting.posting_key}|{platform}".encode()).hexdigest()[:16]
        first_seen = posting.first_seen_at.isoformat() if posting.first_seen_at else "unknown"
        claims.append(
            ProposedSourced(
                claim_kind="sourced",
                local_id=f"job-{digest}",
                attribute_key=f"jobs.{_slug(platform)}.{posting.posting_class}",
                value=platform,
                evidence=(ProposedEvidence(url=posting.url, supporting_sentence=verbatim),),
                reasoning=(
                    f"Posting status {posting.status} ({posting.status_source}), first seen"
                    f" {first_seen}, class {posting.posting_class} by"
                    f" {posting.classification.classified_by}."
                ),
            )
        )
    return claims, skipped
