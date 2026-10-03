"""Posts and comments for priority A only, behind a cost-sample gate (P11).

Candidates are in-scope priority A people with a read profile. A person whose last activity is
older than ``recent_activity_days`` is skipped (not recent); a person whose profile gives no
last-activity date is pulled and flagged ``activity_unfiltered`` (the filter could not be
applied). Posts dated outside the window are dropped; undated posts are kept and the person is
flagged.

Cost gate: the first ``cost_sample_size`` candidates are pulled, the recorded cost of each call is
read from the ledger (``CostPort``) and the total for the rest is projected. The rest is pulled
only when the projection fits ``posts_budget_share`` of the stage budget. An unknown cost
(``None``) is never treated as zero: the pull stops at ``unknown_cost_post_cap`` people and the
outcome is flagged ``cost_unknown``. A person's posts already pulled in this run are reused.
Post text is untrusted: it is sanitised, capped to an excerpt, and a post carrying the canary or
an instruction is stored without its excerpt and counted hostile.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from jobs.normalise import strip_pii
from people.config import PeopleConfig
from people.cues import clean
from people.models import PartialDate, Person, Post, PostsBatch, Profile
from people.ports import BUDGET_REASONS, CostPort, PostsPort, SourceUnavailable
from people.profiles import recent


@dataclass
class PostsOutcome:
    """Posts by person key, flags, ledger keys and the gate's decision."""

    posts: dict[str, tuple[Post, ...]] = field(default_factory=dict)
    requested: set[str] = field(default_factory=set)
    unfiltered: set[str] = field(default_factory=set)
    not_recent: set[str] = field(default_factory=set)
    call_refs: list[str] = field(default_factory=list)
    failed_refs: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)  # reasons of failed pulls, in order
    candidates: int = 0
    cost_unknown: bool = False
    projected_usd: float | None = None
    exit_reason: str = "converged"
    cap_hit: bool = False


def _posts(
    person_key: str,
    batch: PostsBatch,
    since: date,
    cfg: PeopleConfig,
    internal: bool,
    is_hostile: Callable[[str], bool],
) -> tuple[tuple[Post, ...], bool]:
    kept: list[Post] = []
    undated = False
    cap = cfg.cap("max_excerpt_chars")
    for raw in batch.posts[: cfg.cap("max_posts_per_person")]:
        when = PartialDate.parse(raw.posted_at)
        posted = when.earliest() if when is not None else None
        if posted is not None and posted < since:
            continue
        undated |= posted is None
        excerpt = strip_pii(clean(raw.text, cap))
        hostile = bool(excerpt) and is_hostile(excerpt)
        stored = "" if hostile else excerpt
        kept.append(
            Post(
                person_key=person_key,
                post_url=raw.post_url,
                posted_at=posted,
                excerpt=stored,
                excerpt_sha256=hashlib.sha256(stored.encode()).hexdigest(),
                raw_ref=raw.raw_ref,
                call_ref=raw.call_ref,
                internal_only=internal,
                hostile=hostile,
            )
        )
    return tuple(kept), undated


async def pull_posts(
    people: Sequence[Person],
    profiles: Mapping[str, Profile],
    port: PostsPort,
    costs: CostPort,
    stored: Mapping[str, tuple[Post, ...]],
    save: Callable[[str, Sequence[Post]], Awaitable[None]],
    cfg: PeopleConfig,
    *,
    as_of: date,
    budget_usd: float | None,
    is_hostile: Callable[[str], bool],
    out_of_time: Callable[[], bool],
) -> PostsOutcome:
    """The posts loop (see the module docstring)."""
    out = PostsOutcome()
    days = cfg.cap("recent_activity_days")
    since = as_of - timedelta(days=days)
    candidates: list[Person] = []
    for p in people:
        profile = profiles.get(p.profile_url or "")
        if p.status != "in_scope" or p.classification.priority != "A" or profile is None:
            continue
        verdict = recent(profile.last_activity_at, as_of, days)
        if verdict is False:
            out.not_recent.add(p.person_key)
            continue
        if verdict is None:
            out.unfiltered.add(p.person_key)
        candidates.append(p)
    out.candidates = len(candidates)
    sample_size = max(cfg.cap("cost_sample_size"), 1)
    unknown_cap = cfg.cap("unknown_cost_post_cap")
    share = cfg.caps["posts_budget_share"]
    sample_costs: list[float | None] = []
    allowed = len(candidates)
    # LOOP(posts pull): cap=cost_sample_size, then the budget-approved remainder or
    # unknown_cost_post_cap; exit=converged, budget, cap_reached, deadline
    for index, person in enumerate(candidates):
        if index >= allowed:
            out.cap_hit = True
            break
        url = person.profile_url or ""
        if url in stored:
            out.posts[person.person_key] = tuple(
                post.model_copy(update={"person_key": person.person_key}) for post in stored[url]
            )
            out.requested.add(person.person_key)
            continue
        if out_of_time():
            out.exit_reason = "deadline"
            break
        try:
            batch = await port.pull(url, since)
        except SourceUnavailable as error:
            out.failures.append(error.reason)
            if error.call_ref:
                out.failed_refs.append(error.call_ref)
            if error.reason in BUDGET_REASONS:
                out.exit_reason = "budget" if error.reason == "budget_cap" else "deadline"
                break
            continue
        out.requested.add(person.person_key)
        out.call_refs.append(batch.call_ref)
        profile = profiles[url]
        posts, undated = _posts(
            person.person_key, batch, since, cfg, profile.internal_only, is_hostile
        )
        if undated or not batch.activity_known:
            out.unfiltered.add(person.person_key)
        out.posts[person.person_key] = posts
        await save(url, posts)
        if len(sample_costs) < sample_size:
            sample_costs.append(await costs.cost_usd(batch.call_ref))
            if len(sample_costs) == min(sample_size, len(candidates)):
                allowed = _gate(out, sample_costs, len(candidates), budget_usd, share, unknown_cap)
    if out.exit_reason == "converged" and out.cap_hit:
        out.exit_reason = "budget" if not out.cost_unknown else "cap_reached"
    return out


def _gate(
    out: PostsOutcome,
    sample: Sequence[float | None],
    total: int,
    budget_usd: float | None,
    share: float,
    unknown_cap: int,
) -> int:
    """How many candidates may be pulled in all, from the sample's recorded costs."""
    if any(c is None for c in sample) or budget_usd is None:
        out.cost_unknown = True
        return max(min(unknown_cap, total), len(sample))
    known = [c for c in sample if c is not None]
    mean = sum(known) / len(known)
    out.projected_usd = round(mean * total, 6)
    if out.projected_usd <= budget_usd * share:
        return total
    affordable = int((budget_usd * share) // mean) if mean > 0 else total
    return max(min(affordable, total), len(sample))
