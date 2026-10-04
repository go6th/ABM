"""ETag helpers for conditional API reads.

Guarantees: weak and strong validators compare correctly for GET requests
(GUARANTEE(test_etag_matching_supports_weak_tags_and_wildcards)).
"""

from __future__ import annotations


def if_none_match_matches(header: str | None, etag: str) -> bool:
    """Return whether If-None-Match contains the current entity tag."""
    if header is None:
        return False
    for candidate in header.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if value.removeprefix("W/") == etag:
            return True
    return False
