"""People list rules: seniority, exclusions and the merge (roadmap s7 people pipeline, PRD s3.4).

One deduplicated list per account, merged by profile URL first and name second.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.rules.schemas import PeopleScope

# Ordered: the first match wins, so longer titles are tested before the ones they contain.
_LEVEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("C-level", re.compile(r"\bchief\b|\bc[a-z]o\b", re.I)),
    ("EVP", re.compile(r"\bevp\b|\bexecutive vice president\b", re.I)),
    ("SVP", re.compile(r"\bsvp\b|\bsenior vice president\b", re.I)),
    ("VP", re.compile(r"\bvp\b|\bvice president\b", re.I)),
    ("Head", re.compile(r"\bhead of\b|\bhead,", re.I)),
    ("Director", re.compile(r"\bdirector\b", re.I)),
    ("Senior Manager", re.compile(r"\bsenior manager\b|\bsr\.? manager\b", re.I)),
]


def infer_level(title: str) -> str | None:
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.search(title):
            return level
    return None


def normalize_profile_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip() if "://" in url else f"https://{url.strip()}")
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    return f"{host}{path}" if host else None


def normalize_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", folded).split())


@dataclass(frozen=True)
class PersonRecord:
    name: str
    title: str
    profile_url: str | None
    source: str


@dataclass
class MergedPerson:
    name: str
    title: str
    profile_url: str | None
    level: str | None
    sources: set[str] = field(default_factory=set)


@dataclass
class PeopleList:
    people: list[MergedPerson]
    excluded: list[PersonRecord]
    out_of_scope: list[PersonRecord]

    def keys(self) -> set[str]:
        return {person_key(p) for p in self.people}


def person_key(p: MergedPerson) -> str:
    return normalize_profile_url(p.profile_url) or f"name:{normalize_name(p.name)}"


def is_excluded(title: str, scope: PeopleScope) -> bool:
    lowered = title.casefold()
    return any(term.casefold() in lowered for term in scope.excluded_title_terms)


def merge_people(records: list[PersonRecord], scope: PeopleScope) -> PeopleList:
    """Apply exclusions and seniority, then merge by profile URL, then by name."""
    merged: list[MergedPerson] = []
    by_url: dict[str, MergedPerson] = {}
    by_name: dict[str, MergedPerson] = {}
    excluded: list[PersonRecord] = []
    out_of_scope: list[PersonRecord] = []

    for rec in records:
        if is_excluded(rec.title, scope):
            excluded.append(rec)
            continue
        level = infer_level(rec.title)
        if level is None or level not in scope.levels_in_scope:
            out_of_scope.append(rec)
            continue
        url = normalize_profile_url(rec.profile_url)
        name = normalize_name(rec.name)
        match = by_url.get(url) if url else None
        if match is None:
            candidate = by_name.get(name)
            # A name match never merges two people who both have different profile URLs.
            candidate_url = normalize_profile_url(candidate.profile_url) if candidate else None
            if candidate is not None and not (url and candidate_url and url != candidate_url):
                match = candidate
        if match is None:
            match = MergedPerson(rec.name, rec.title, rec.profile_url, level)
            merged.append(match)
        elif match.profile_url is None and rec.profile_url:
            match.profile_url = rec.profile_url
        match.sources.add(rec.source)
        if url:
            by_url[url] = match
        by_name.setdefault(name, match)
    return PeopleList(merged, excluded, out_of_scope)
