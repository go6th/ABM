"""People matched to the profile pages the research already holds (pure).

Phase 1 searches the open web for the account's key contacts ("{account} chief information
officer OR CIO") and stores every result it saw, LinkedIn profile pages among them, as archived
copies (``account_intelligence.archive``). The people source, by contrast, returns some people
without a profile link, and a person named in a source (an executive quoted in a release) has
no link at all. Both end off the People List ("no profile link yet"). Here a name is matched to
the archived profile pages: a search result's title reads "Jane Doe - Chief Information Officer
- Acme Corp | LinkedIn", so the name, the headline and the company come from the title alone.
A match needs the whole normalised name to be equal (``people.names``); the company part of the
title names the account's brand for the record to carry the account as the current employer,
otherwise the employer stays unknown and the scope rules decide (review). No paid call is made.

GUARANTEE(test_a_person_without_a_link_is_matched_to_the_archived_profile_page)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from people.models import RawPerson
from people.names import names_equal

_SUFFIX = re.compile(r"\s*[|\-–—:]\s*LinkedIn\s*$", re.IGNORECASE)
_SEPARATOR = re.compile(r"\s+[-–—|]\s+")
_NOISE = re.compile(r"^\s*(?:profile|on linkedin)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ProfileTitle:
    """The parts of a profile page's title: the person, the headline, the company."""

    name: str
    headline: str | None = None
    company: str | None = None


@dataclass(frozen=True)
class ProfileLink:
    """One archived profile page as the matcher needs it."""

    url: str
    title: str | None
    payload_ref: str
    archived_at: str  # ISO 8601


def parse_profile_title(title: str | None) -> ProfileTitle | None:
    """The person, headline and company in a profile page's title, or None when the title does
    not read as a person's page ("Acme Corp | LinkedIn", an empty title)."""
    if not title:
        return None
    body = _SUFFIX.sub("", " ".join(title.split()))
    parts = [p.strip() for p in _SEPARATOR.split(body) if p.strip() and not _NOISE.match(p)]
    if not parts:
        return None
    name = parts[0]
    if len(name) < 3 or len(name.split()) < 2 or len(name.split()) > 6:
        return None
    if len(parts) == 1:
        return ProfileTitle(name)
    if len(parts) == 2:
        return ProfileTitle(name, headline=parts[1])
    return ProfileTitle(name, headline=" - ".join(parts[1:-1]), company=parts[-1])


def _names_brand(company: str | None, brand: str | None) -> bool:
    if not company or not brand:
        return False
    words = {w for w in re.findall(r"[a-z0-9]+", company.casefold())}
    return brand.casefold() in words


def candidates_for(
    name: str,
    links: Iterable[ProfileLink],
    *,
    account_domain: str,
    brand: str | None,
) -> list[RawPerson]:
    """The archived profile pages whose title names ``name``, as records for the people pipeline
    (identity data from the page title; the archived copy is the record's reference)."""
    out: list[RawPerson] = []
    for link in links:
        parsed = parse_profile_title(link.title)
        if parsed is None or not names_equal(parsed.name, name):
            continue
        at_account = _names_brand(parsed.company, brand)
        digest = hashlib.sha256(link.url.encode()).hexdigest()[:32]
        out.append(
            RawPerson(
                source_record_id=f"archive:{digest}",
                origin="title_search",
                raw_ref=link.payload_ref,
                retrieved_at=link.archived_at,
                name=parsed.name,
                title=parsed.headline,
                headline=parsed.headline,
                profile_url=link.url,
                employer_domain=account_domain if at_account else None,
                is_current=True if at_account else None,
            )
        )
    return out


def unlinked(records: Sequence[RawPerson]) -> list[RawPerson]:
    """The records with a name and no profile link (the people source gave none)."""
    return [r for r in records if r.name and not r.profile_url]
