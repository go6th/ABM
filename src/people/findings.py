"""Findings from LinkedIn: the tool-less extractor wrapper (P13).

Only a person's own description and their non-hostile post excerpts are sent, sanitised and
capped, with the canary; never a tool, a URL to fetch or another person's text. The answer is
untrusted and must be exactly ``{person_key, findings: [{claim, sentence}]}``: any other field
(for example an attempt to include or exclude someone) fails the schema. Each finding must:

* carry a ``sentence`` found verbatim (after whitespace normalisation) in THIS person's own text;
  its URL is the profile (description) or the post it came from;
* make a ``claim`` whose capitalised words all appear in that sentence, so the extractor cannot
  add a person, an employer or a title the sentence does not name;
* contain no email address or phone number.

An answer that echoes the canary or an instruction marks the person ``hostile`` and nothing from
it is used. Every drop is counted by reason. Accepted findings become ``ProposedSourced`` claims
for the grading engine, which re-checks the span (G4); the pipeline never sets a grade.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_intelligence.grading.schema import ProposedEvidence, ProposedSourced
from account_intelligence.grading.spans import normalise_ws
from jobs.normalise import strip_pii
from people.models import Finding, Person, Post, Profile
from people.ports import ExtractorPort

_PROPER = re.compile(r"\b[A-Z][\w&.'-]*")


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    claim: str = Field(min_length=1, max_length=400)
    sentence: str = Field(min_length=1, max_length=2000)


class ExtractorAnswer(BaseModel):
    """The only shape an extractor answer may take."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    person_key: str
    # a list, not a tuple: strict mode refuses a JSON array for a tuple field
    findings: list[_Item] = Field(max_length=10)


@dataclass
class FindingsOutcome:
    """Accepted findings, hostile people and drop counts by reason."""

    findings: list[Finding] = field(default_factory=list)
    hostile: set[str] = field(default_factory=set)
    dropped: Counter[str] = field(default_factory=Counter)
    calls: int = 0
    exit_reason: str = "converged"


def _texts(profile: Profile, posts: Sequence[Post]) -> list[tuple[str, str]]:
    """(text, url) pairs of the person's own words."""
    pairs: list[tuple[str, str]] = []
    if profile.own_description and not profile.hostile:
        pairs.append((profile.own_description, profile.profile_url))
    pairs.extend((p.excerpt, p.post_url) for p in posts if p.excerpt and not p.hostile)
    return pairs


def _source_of(sentence: str, pairs: Sequence[tuple[str, str]]) -> str | None:
    needle = normalise_ws(sentence)
    for text, url in pairs:
        if needle and needle in normalise_ws(text):
            return url
    return None


def supported(claim: str, sentence: str) -> bool:
    """Every capitalised word of the claim appears in the sentence (case-insensitive)."""
    words = {w.casefold() for w in re.findall(r"[\w&'-]+", sentence)}
    return all(token.strip(".").casefold() in words for token in _PROPER.findall(claim))


async def extract_findings(
    people: Sequence[Person],
    profiles: dict[str, Profile],
    posts: dict[str, tuple[Post, ...]],
    extractor: ExtractorPort,
    *,
    canary: str,
    markers: Sequence[str],
    max_calls: int,
    max_per_person: int,
    max_chars: int,
) -> FindingsOutcome:
    """Findings for in-scope people with a read, non-hostile profile."""
    out = FindingsOutcome()
    # LOOP(findings extractor): cap=max_extractor_calls, one call per person; exit=converged,
    # cap_reached
    for person in people:
        profile = profiles.get(person.profile_url or "")
        if person.status != "in_scope" or profile is None or profile.hostile:
            continue
        pairs = _texts(profile, posts.get(person.person_key, ()))
        if not pairs:
            continue
        if out.calls >= max_calls:
            out.exit_reason = "cap_reached"
            break
        out.calls += 1
        text = "\n\n".join(t for t, _ in pairs)[:max_chars]
        raw = await extractor(person.person_key, text, canary)
        rendered = str(raw).casefold()
        if canary.casefold() in rendered or any(m.casefold() in rendered for m in markers):
            out.hostile.add(person.person_key)
            out.dropped["hostile"] += 1
            continue
        try:
            answer = ExtractorAnswer.model_validate(raw)
        except ValidationError:
            out.dropped["schema_invalid"] += 1
            continue
        if answer.person_key != person.person_key:
            out.dropped["person_mismatch"] += 1
            continue
        for item in answer.findings[:max_per_person]:
            url = _source_of(item.sentence, pairs)
            if url is None:
                out.dropped["sentence_not_in_text"] += 1
            elif strip_pii(item.claim) != item.claim or strip_pii(item.sentence) != item.sentence:
                out.dropped["contact_detail"] += 1
            elif not supported(item.claim, item.sentence):
                out.dropped["claim_not_in_sentence"] += 1
            else:
                out.findings.append(
                    Finding(
                        person_key=person.person_key,
                        claim=normalise_ws(item.claim),
                        sentence=item.sentence,
                        url=url,
                    )
                )
        dropped_extra = len(answer.findings) - min(len(answer.findings), max_per_person)
        if dropped_extra:
            out.dropped["over_person_cap"] += dropped_extra
    return out


def finding_claims(findings: Sequence[Finding]) -> list[ProposedSourced]:
    """Findings as sourced claims (attribute ``people.<person_key>.finding``)."""
    claims = []
    for f in findings:
        digest = hashlib.sha256(f"{f.person_key}|{f.sentence}".encode()).hexdigest()[:16]
        claims.append(
            ProposedSourced(
                claim_kind="sourced",
                local_id=f"finding-{digest}",
                attribute_key=f"people.{f.person_key}.finding",
                value=f.claim,
                evidence=(ProposedEvidence(url=f.url, supporting_sentence=f.sentence),),
            )
        )
    return claims
