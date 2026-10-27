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


# a "name" that starts with one of these is a title or a fragment, never a person (master prompt
# v2 section 5: 136 of 228 "people" of one run were job titles or quotes)
_TITLE_WORDS = frozenset(
    {
        "president", "senior", "vice", "chief", "director", "head", "manager", "lead", "global",
        "the", "our", "a", "an", "team", "group", "office", "department", "principal", "staff",
        "executive", "associate", "assistant", "regional", "national", "general", "deputy",
    }
)  # fmt: skip
# the words of a function or a role: a "name" made of them ("Technology Product Management", the
# rest of "Vice President of Technology Product Management") is a title, never a person
ROLE_WORDS = frozenset(
    {
        "officer", "technology", "technologies", "information", "product", "products",
        "management", "operations", "engineering", "marketing", "sales", "finance", "financial",
        "digital", "data", "strategy", "transformation", "business", "development", "analytics",
        "security", "solutions", "services", "systems", "infrastructure", "architecture",
        "enterprise", "customer", "commercial", "revenue", "platform", "platforms",
        "applications", "innovation", "procurement", "partnerships", "program", "programme",
        "project", "projects", "human", "resources", "communications", "chairman", "chairwoman",
        "chair", "founder", "cofounder", "co-founder", "partner", "board", "committee",
        "investor", "relations", "counsel", "legal", "supply", "chain", "quality", "research",
        "manufacturing", "clinical", "medical", "regulatory", "affairs", "compliance", "risk",
        "audit", "treasury", "controller", "accounting", "talent", "people", "experience",
        "growth", "channel", "alliances", "practice", "centre", "center", "excellence", "it",
        "hr",
    }
)  # fmt: skip
_NOT_NAME = _TITLE_WORDS | ROLE_WORDS
# lower-case surname particles ("Pieter van der Berg", "Maria de la Cruz", "Ahmed bin Zayed")
_PARTICLES = frozenset(
    {
        "van", "von", "der", "den", "de", "da", "das", "dos", "do", "del", "della", "di", "du",
        "la", "le", "ter", "ten", "bin", "binti", "al", "el", "zu", "af",
    }
)  # fmt: skip
_NAME_MARKS = frozenset("'’.-")
# a short parenthesised aside after a name: pronouns, a maiden name ("Jane Doe (she/her)")
_ASIDE = re.compile(r"\([^()]{0,40}\)")


def _name_token(token: str) -> str | None:
    """``"name"`` for a capitalised word in any script (an initial counts, and so does a word of
    a script without letter case, such as Chinese or Arabic), ``"particle"`` for a lower-case
    surname particle, else ``None``. Letters, apostrophes, hyphens and dots only."""
    if token in _PARTICLES:
        return "particle"
    if not all(ch.isalpha() or ch in _NAME_MARKS for ch in token):
        return None
    first = next((ch for ch in token if ch.isalpha()), "")
    if first.isupper() or (first and not first.islower()):
        return "name"
    return None


def _caseless(token: str) -> bool:
    """A word of a script without letter case (a one-word name such as 王小明 is written so)."""
    return len(token) >= 2 and all(
        ch.isalpha() and not ch.isupper() and not ch.islower() for ch in token
    )


def looks_like_person(name: str | None) -> bool:
    """A record is a person only when its name reads like one: two to five tokens, each
    capitalised in its own script (an initial counts, so do lower-case surname particles such as
    "van der" after the first word, and a short parenthesised aside such as pronouns is ignored),
    at least two of them name words, none of them a title or role word, no digits, no quote marks; a
    single word only in a script without letter case. A title-only string ("Senior Vice
    President, Marketing") or a quoted sentence is not a person row, whatever the source called
    it. Accented and non-Latin names ("José Müller", "Łukasz Nowak", "王小明") are people: until
    2026-10-27 only A to Z letters passed and such people were excluded with their profile
    links."""
    if not name:
        return False
    head = name.strip().strip('"“”')
    if not (2 <= len(head) <= 80) or any(ch.isdigit() for ch in head) or '"' in head:
        return False
    tokens = _ASIDE.sub(" ", head).replace(",", " ").split()
    if len(tokens) == 1:
        return _caseless(tokens[0])
    if not 2 <= len(tokens) <= 5 or len(head) < 4:
        return False
    if any(t.rstrip(".").lower() in _NOT_NAME for t in tokens):
        return False
    kinds = [_name_token(t) for t in tokens]
    if any(k is None for k in kinds) or kinds[0] != "name":
        return False
    return sum(1 for k in kinds if k == "name") >= 2
