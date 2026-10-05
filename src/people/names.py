"""Name normalisation and comparison for the name-second merge rule (P3). Pure.

The normal form is NFKC, case-folded, with combining marks removed and every run of characters
that are not letters or digits replaced by one space. No honorific, suffix or nickname list is
applied: none is in the sources (D-21), so "Dr Jane Doe" and "Jane Doe" do not compare equal.
"""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w]+|_+")


def normalise_name(name: str | None) -> str:
    """The comparison form of a person's name ('' when absent)."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_NON_WORD.sub(" ", stripped.casefold()).split())


def names_equal(left: str | None, right: str | None) -> bool:
    """Both names present and equal in normal form."""
    a, b = normalise_name(left), normalise_name(right)
    return bool(a) and a == b


def title_tokens(title: str | None) -> frozenset[str]:
    """Word tokens of a title in name normal form."""
    return frozenset(normalise_name(title).split())


def titles_similar(left: frozenset[str], right: frozenset[str], threshold: float) -> bool:
    """Jaccard similarity of two token sets at or above ``threshold`` (both non-empty)."""
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= threshold
