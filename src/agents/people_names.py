"""Person-name searches from the executives the research found (checklist step 9).

The skill searches speaking, podcasts and video by person name, never by company name, once
the people list exists ("company name plus the word conference returns noise"). The people step
is off (OI-ABM-4), so the names come from the executives the reading step graded: the
``executive_role`` and ``appointment`` facts, whose values quote the page ("Brian J. Blaser,
President and Chief Executive Officer"). Code takes the name before the first comma, keeps only
what looks like a person's name (two to five words of letters, initials and hyphens), and builds
one query per person and row from a fixed template. A name is data from a page, so it is
bounded and checked before it becomes a query; it is never an instruction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from agents.schemas import StoredFact
from people.names import looks_like_person

NAME_ATTRIBUTES = frozenset({"executive_role", "appointment", "departure"})
# the attributes whose meaning is a named person (``config/skill/attributes.yaml``: "A named
# technology, data, digital or commercial leader with the title as reported"): a value that names
# nobody ("Vice President of Technology Product Management") is a role, not a person, and is
# dropped at extraction, counted (2026-10-27: such values were shown as people with no profile
# link). Appointments and departures stay: an unnamed appointment is still a leadership signal.
NAMED_PERSON_ATTRIBUTES = frozenset({"executive_role"})
# role abbreviations written in capitals; a "name" made with one ("CIO EMEA") names nobody
_ROLE_ABBREVIATIONS = frozenset(
    {
        "ceo", "cio", "cto", "cfo", "coo", "cdo", "cmo", "ciso", "cro", "cpo", "cso", "cco",
        "cao", "chro", "cdao", "cxo", "cno", "svp", "evp", "vp", "avp", "gm", "md", "it", "hr",
        "ai", "emea", "apac", "latam", "amer", "na", "us", "uk", "eu", "anz", "r&d",
    }
)  # fmt: skip
_SEGMENTS = re.compile(r"[,;:()\[\]|/\u2013\u2014]+| - ")
MAX_NAME_WORDS = 5
# the rows the person-name searches serve, with the query template of each (phase1-pull.md step
# 10: "[Name] [Account] interview OR podcast OR speaker OR keynote")
PERSON_ROWS: dict[str, str] = {
    "executive_speaking": "{name} {account} speaker OR keynote OR panel OR conference",
    "podcasts": "{name} {account} podcast OR interview",
    "youtube": "{name} {account} video OR webinar OR youtube",
}
MAX_PEOPLE = 10
_WORD = r"[A-Z][a-zA-Z'\-]+\.?|[A-Z]\."
_NAME = re.compile(rf"^(?:{_WORD})(?:\s+(?:{_WORD})){{1,4}}$")
_TITLE_WORDS = frozenset(
    {"chief", "officer", "president", "vice", "director", "head", "senior", "executive"}
)


def person_name(value: str) -> str | None:
    """The person's name a fact value starts with, or None when the value is not one."""
    head = re.split(r"[,;(–—|]| - | at | of ", value.strip(), maxsplit=1)[0].strip()
    head = re.sub(r"^(Dr|Mr|Ms|Mrs)\.?\s+", "", head)
    if not (4 <= len(head) <= 60) or not _NAME.match(head):
        return None
    words = {w.rstrip(".").lower() for w in head.split()}
    if words & _TITLE_WORDS:
        return None
    return head


def names_a_person(value: str) -> bool:
    """True when ``value`` names a person somewhere ("Jane Doe, CIO", "Chief Information Officer
    Jane Doe", "José Müller"): some run of two to five words in one part of it reads like a
    person's name by the people module's rule (``people.names.looks_like_person``) and holds no
    role abbreviation. A value made only of a title and its function words names nobody."""
    for segment in _SEGMENTS.split(value[:300]):
        words = segment.split()
        for start in range(len(words)):
            for size in range(2, MAX_NAME_WORDS + 1):
                window = words[start : start + size]
                if len(window) < size:
                    break
                if any(w.rstrip(".").casefold() in _ROLE_ABBREVIATIONS for w in window):
                    continue
                if looks_like_person(" ".join(window)):
                    return True
    return False


def people_named(facts: Iterable[StoredFact], limit: int | None = None) -> tuple[str, ...]:
    """Distinct names from the executive facts, in fact order, at most ``limit`` (the skill's
    top ten; every executive found when the research caps are off)."""
    from account_intelligence.dev_limits import UNCAPPED, research_uncapped

    if limit is None:
        limit = UNCAPPED if research_uncapped() else MAX_PEOPLE
    out: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact.attribute not in NAME_ATTRIBUTES or fact.visibility != "public":
            continue
        name = person_name(fact.value)
        if name is None or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(name)
        if len(out) >= limit:
            break
    return tuple(out)


def person_queries(
    names: Sequence[str], account: str, rows: Iterable[str]
) -> list[tuple[str, str]]:
    """``(row_key, query)`` for every name and served row in scope."""
    return [
        (row, PERSON_ROWS[row].format(name=name, account=account)[:200])
        for name in names
        for row in rows
        if row in PERSON_ROWS
    ]
