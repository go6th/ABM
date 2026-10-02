"""Safe internal job-posting read model with derived PII-clean text."""

from jobs.models import Posting
from jobs.normalise import clean_text


def export_rows(postings: list[Posting]) -> list[dict[str, object]]:
    """Render selected fields; never expose provider identifiers or raw references."""
    return [
        {
            "class": p.classification.label.value if p.classification else "unclassified",
            "platforms": list(p.platforms),
            "platform_sentences": [s.text for s in p.sentences],
            "description": clean_text(p.description or ""),
            "url": p.url,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "status": p.status,
            "lane": p.lane,
        }
        for p in postings
    ]
