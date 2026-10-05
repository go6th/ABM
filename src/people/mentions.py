"""Resolve people named in research to profile URLs (P2, spec 7 mention loop).

Each mention gets at most ``mention_queries_per_person`` resolver queries and the run at most
``mention_run_cap``. A mention is resolved only when exactly ONE canonical profile URL among the
candidates belongs to a person whose normalised name equals the mentioned name; zero or several
leave it ``unresolved`` with its mention URL and sentence (never a guess). A resolved mention
becomes the candidate's record (identity data from the connector record, P1) carrying the mention
URL and sentence as evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from people.config import PeopleConfig
from people.cues import clean
from people.models import Mention, RawPerson
from people.names import names_equal
from people.ports import BUDGET_REASONS, MentionResolverPort, SourceUnavailable
from people.urls import canonical_profile_url


@dataclass
class MentionOutcome:
    """Records for the merge batch, the ledger keys and the loop exit."""

    records: list[RawPerson] = field(default_factory=list)
    call_refs: list[str] = field(default_factory=list)
    failed_refs: list[str] = field(default_factory=list)
    resolved: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    exit_reason: str = "converged"


def mention_id(mention: Mention) -> str:
    """Stable record id of a mention (name, URL and sentence)."""
    digest = hashlib.sha256(
        f"{mention.name}|{mention.mention_url}|{mention.sentence}".encode()
    ).hexdigest()
    return f"mention:{digest[:32]}"


def _unresolved(mention: Mention, max_chars: int) -> RawPerson:
    return RawPerson(
        source_record_id=mention_id(mention),
        origin="mention",
        name=clean(mention.name, 200),
        mention_url=mention.mention_url,
        mention_sentence=clean(mention.sentence, max_chars),
    )


async def resolve_mentions(
    mentions: Sequence[Mention],
    resolver: MentionResolverPort,
    cfg: PeopleConfig,
    out_of_time: Callable[[], bool],
) -> MentionOutcome:
    """Resolve each mention within the caps (see the module docstring)."""
    out = MentionOutcome()
    per_person = cfg.cap("mention_queries_per_person")
    run_cap = cfg.cap("mention_run_cap")
    max_chars = cfg.cap("max_text_chars")
    queries = 0
    unique = {mention_id(m): m for m in mentions}
    # LOOP(mention resolution): cap=mention_queries_per_person each and mention_run_cap in total;
    # exit=converged, cap_reached, budget, deadline
    for mention in unique.values():
        candidate: RawPerson | None = None
        for attempt in range(per_person):
            if out.exit_reason in ("budget", "deadline"):
                break  # stopped: every remaining mention is kept as unresolved below
            if queries >= run_cap:
                out.exit_reason = "cap_reached"
                break
            if out_of_time():
                out.exit_reason = "deadline"
                break
            queries += 1
            try:
                page = await resolver.resolve(mention.name, attempt)
            except SourceUnavailable as error:
                if error.call_ref:
                    out.failed_refs.append(error.call_ref)
                if error.reason in BUDGET_REASONS:
                    out.exit_reason = "budget"
                continue
            if page.call_ref:
                out.call_refs.append(page.call_ref)
            matches: dict[str, RawPerson] = {}
            for record in page.records:
                url = canonical_profile_url(record.profile_url, cfg.profile_url)
                if url and names_equal(record.name, mention.name):
                    matches.setdefault(url, record)
            if len(matches) == 1:
                candidate = next(iter(matches.values()))
                break
            if len(matches) > 1:
                out.ambiguous += 1
                break
        if candidate is not None:
            out.resolved += 1
            out.records.append(
                candidate.model_copy(
                    update={
                        "origin": "mention",
                        "mention_url": mention.mention_url,
                        "mention_sentence": clean(mention.sentence, max_chars),
                    }
                )
            )
        else:
            out.unresolved += 1
            out.records.append(_unresolved(mention, max_chars))
    return out
