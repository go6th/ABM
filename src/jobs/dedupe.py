"""Stable row deduplication preserving source URLs."""

from jobs.models import RawPosting
from jobs.normalise import canonical_url, posting_key


def dedupe(rows: list[RawPosting]) -> list[RawPosting]:
    """Merge duplicate IDs/URLs/fingerprints, retaining returned URL variants."""
    merged: dict[str, RawPosting] = {}
    urls: dict[str, set[str]] = {}
    for row in rows:
        if not canonical_url(row.url):
            continue
        key = str(posting_key(row))
        current = merged.get(key)
        urls.setdefault(key, set()).update(
            u for u in (canonical_url(row.url), *(canonical_url(x) for x in row.other_urls)) if u
        )
        if current is None:
            merged[key] = row
        else:
            # Prefer a complete description; identity remains stable and order independent.
            chosen = max(
                (current, row),
                key=lambda x: (x.text_complete is True, len(x.description or ""), x.retrieved_at),
            )
            merged[key] = chosen.model_copy(
                update={"other_urls": tuple(sorted(urls[key] - {canonical_url(chosen.url)}))}
            )
    return [
        merged[key].model_copy(
            update={"other_urls": tuple(sorted(urls[key] - {canonical_url(merged[key].url)}))}
        )
        for key in sorted(merged)
    ]
