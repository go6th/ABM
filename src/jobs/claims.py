"""Source-linked claim templates; grading remains an external responsibility."""

from jobs.models import Posting


def claims_for(postings: list[Posting]) -> list[dict[str, object]]:
    """Build claims only for classified, read rows with URL and platform evidence."""
    claims: list[dict[str, object]] = []
    for posting in postings:
        if not posting.classification or posting.classification.label.value == "unclear":
            continue
        evidence = next((s for s in posting.sentences if s.platform in posting.platforms), None)
        if evidence is None or not posting.url:
            continue
        claim_text = f"A {posting.classification.label.value} posting names {evidence.platform}."
        claims.append(
            {
                "text": claim_text,
                "source_url": posting.url,
                "evidence": evidence.text,
                "posting_key": posting.posting_key,
                "status": posting.status,
                "first_seen_at": posting.first_seen_at.isoformat()
                if posting.first_seen_at
                else None,
            }
        )
    return claims
