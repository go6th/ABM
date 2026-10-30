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


def claim_id(posting_key: str, platform: str) -> str:
    """The ``local_id`` of the claim for a posting and a platform it names (stable: the same
    posting and platform give the same id in every research, so a later pass can tell which
    claims belong to which posting)."""
    digest = hashlib.sha256(f"{posting_key}|{platform}".encode()).hexdigest()[:16]
    return f"job-{digest}"


def claimable(posting: Posting) -> bool:
    """True when the posting is read in full and classed by a reading: the only postings that
    make a claim (a keyword rule over the sentences is not a reading: never a finding)."""
    if posting.classification is None or posting.posting_class == "unclassified":
        return False
    if posting.classification.classified_by == "heuristic":
        return False
    return not (not posting.read or posting.title_only or posting.hostile)


def claims_for(posting: Posting, text: str) -> tuple[list[ProposedSourced], int]:
    """Claims for one posting from its stored ``text``; returns ``(claims, skipped_for_pii)``."""
    classification = posting.classification
    if classification is None or not claimable(posting):
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
        first_seen = posting.first_seen_at.isoformat() if posting.first_seen_at else "unknown"
        claims.append(
            ProposedSourced(
                claim_kind="sourced",
                local_id=claim_id(posting.posting_key, platform),
                attribute_key=f"jobs.{_slug(platform)}.{posting.posting_class}",
                value=platform,
                evidence=(ProposedEvidence(url=posting.url, supporting_sentence=verbatim),),
                reasoning=(
                    f"Posting status {posting.status} ({posting.status_source}), first seen"
                    f" {first_seen}, class {posting.posting_class} by"
                    f" {classification.classified_by}."
                ),
            )
        )
    return claims, skipped
