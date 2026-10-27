"""The people pipeline (PRD step 7) over injected ports.

Spec: docs/people/rubric.md and docs/people/counts.md.

Steps: (1) title search per (lane, pass), paged on the same source from its cursor; (2) normalise
and merge (union-find, P3); (3) scope (P4, P5); (4) gaps: one association search per gap lane,
then re-merge and re-scope; (5) people named in research, resolved to profile URLs (P2); (6) full
profiles for every in-scope person, pulled once (P10); (7) posts for priority A only behind the
cost-sample gate (P11); (8) counts from rows (P6 to P8); (9) Findings from the extractor (P13);
(10) the record, templated claims, row evidence and ``top_people``.

Guarantees: every found record ends in exactly one status and ``found = distinct + merged_away``
(checked by ``people.counts``); every loop is bounded and records its exit reason; one person's
failure never aborts a batch; no status, count, level, priority or grade is set by a model or a
caller; untrusted text reaches only the tool-less extractor and is never logged; the pipeline adds
no retry (the node ``RetryPolicy`` owns retries).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from account_intelligence.grading.schema import ProposedSourced
from jobs.normalise import strip_pii
from people.claims import title_claims
from people.config import PeopleConfig
from people.counts import compute_counts, render_count_sentences
from people.cues import clean
from people.errors import InvariantViolation, PeopleError
from people.findings import extract_findings, finding_claims
from people.gaps import association_specs, gap_lanes
from people.lane import lanes_for
from people.mentions import resolve_mentions, resolve_unlinked
from people.merge import MergeEvent, MergeItem, MergePlan, stable_plan
from people.models import (
    Classification,
    Finding,
    PeopleCounts,
    Person,
    PersonPage,
    Post,
    Profile,
    RawPerson,
)
from people.names import looks_like_person, normalise_name, title_tokens
from people.ports import (
    CostPort,
    ExtractorPort,
    MentionResolverPort,
    MentionsPort,
    PeopleSearchPort,
    PostsPort,
    ProfilePort,
    SourceUnavailable,
)
from people.posts import pull_posts
from people.profiles import pull_profiles
from people.scope import ScopeInput, classify
from people.search import SearchSpec, SourceOutcome, page_source, plan_title_searches
from people.urls import canonical_profile_url
from tenancy.settings import ExclusionFilter

ROW_PEOPLE = "people_by_platform"
ROW_PROFILES = "full_profiles_senior_people"
ROW_POSTS = "posts_priority_people"
OWNED_ROWS = (ROW_PEOPLE, ROW_PROFILES, ROW_POSTS)
LEVEL_RANK_MISSING = 99


@dataclass(frozen=True)
class RowEvidence:
    """Evidence for one owned tracker row: ledger keys only (the tracker derives the status)."""

    row_key: str
    call_refs: tuple[str, ...]
    expected_calls: int
    cap_hit: bool
    not_attempted_reason: str | None = None


@dataclass(frozen=True)
class RunInputs:
    """The run inputs one pass uses (read once from ``RunInputPort``)."""

    account_domain: str
    as_of: date
    lanes_off: frozenset[str]
    regions: tuple[str, ...] = ()
    reference_employers: tuple[str, ...] | None = None
    budget_usd: float | None = None
    excluded_people: tuple[str, ...] = ()
    # the entity lock's other domains of the account (redirect host, subsidiaries): an employer
    # there is the account, not another company (2026-10-24)
    allowed_domains: tuple[str, ...] = ()


def tenant_excludes(inputs: RunInputs, name: str | None, profile_url: str | None) -> bool:
    """The tenant's exclusion (``tenancy.settings.ExclusionFilter``, the one implementation)
    applied to one found person at pull time (TN5)."""
    if not inputs.excluded_people:
        return False
    rule = ExclusionFilter(frozenset(), frozenset(inputs.excluded_people))
    return rule.excludes_person(name=name, profile_url=profile_url)


@dataclass(frozen=True)
class PipelineResult:
    """Everything one pass produced; persisted as the run-level record."""

    run_id: str
    as_of: date
    config_hash: str
    people: tuple[Person, ...]
    profiles: tuple[Profile, ...]
    posts: tuple[Post, ...]
    findings: tuple[Finding, ...]
    merge_events: tuple[MergeEvent, ...]
    counts: PeopleCounts | None
    count_sentences: tuple[str, ...]
    invariant_error: str | None
    exit_reasons: dict[str, str]
    evidence: tuple[RowEvidence, ...]
    claims: tuple[ProposedSourced, ...]
    lanes_not_searched: dict[str, str]
    source_codes: dict[str, str]
    drops: dict[str, int]
    flags: dict[str, bool]
    findings_hostile: tuple[str, ...] = ()
    calls_made: int = 0
    replayed: bool = False

    def top_people(self, n: int, level_order: Sequence[str]) -> tuple[Person, ...]:
        """In-scope people in the D-21 order: priority, then level, then the stable key."""
        return top_people(self.people, n, level_order)


def top_people(people: Sequence[Person], n: int, level_order: Sequence[str]) -> tuple[Person, ...]:
    """In-scope people ordered by priority, level (``level_order``), then person key."""
    rank = {level: i for i, level in enumerate(level_order)}
    chosen = [p for p in people if p.status == "in_scope"]
    chosen.sort(
        key=lambda p: (
            p.classification.priority or "Z",
            rank.get(p.classification.level or "", LEVEL_RANK_MISSING),
            p.person_key,
        )
    )
    return tuple(chosen[: max(n, 0)])


def person_key(source_record_id: str) -> str:
    """Stable key of one found record (lower-case hex, valid in an attribute key)."""
    return "p" + hashlib.sha256(source_record_id.encode()).hexdigest()[:24]


@dataclass
class _Work:
    """Mutable working state of one pass (never leaves this module)."""

    records: list[RawPerson] = field(default_factory=list)
    outcomes: list[SourceOutcome] = field(default_factory=list)
    seen_records: set[str] = field(default_factory=set)
    seen_people: set[str] = field(default_factory=set)
    exits: dict[str, str] = field(default_factory=dict)
    # records that came without a profile link and were given one from the archived pages
    linked: int = 0


class PeoplePipeline:
    """Orchestrates the steps; every dependency is a port."""

    def __init__(
        self,
        cfg: PeopleConfig,
        search: PeopleSearchPort,
        mentions: MentionsPort,
        resolver: MentionResolverPort,
        profiles: ProfilePort,
        posts: PostsPort,
        costs: CostPort,
        extractor: ExtractorPort,
        repo: Any,
        *,
        canary: str,
        hostile_markers: tuple[str, ...],
        is_hostile: Callable[[str], bool],
        out_of_time: Callable[[], bool] = lambda: False,
    ) -> None:
        self.cfg = cfg
        self.search = search
        self.mentions = mentions
        self.resolver = resolver
        self.profile_port = profiles
        self.posts_port = posts
        self.costs = costs
        self.extractor = extractor
        self.repo = repo
        self.canary = canary
        self.markers = hostile_markers
        self.is_hostile = is_hostile
        self.out_of_time = out_of_time

    # ---- rows -----------------------------------------------------------------------------

    def _merge_item(self, record: RawPerson) -> MergeItem:
        return MergeItem(
            key=person_key(record.source_record_id),
            url=canonical_profile_url(record.profile_url, self.cfg.profile_url),
            name=normalise_name(record.name),
            employer_domain=(record.employer_domain or "").lower() or None,
            lanes=frozenset(
                m.lane for m in lanes_for(record.title, record.headline, self.cfg.lanes)
            ),
            title_tokens=title_tokens(record.title),
        )

    def build_rows(
        self, records: Sequence[RawPerson], inputs: RunInputs
    ) -> tuple[list[Person], MergePlan]:
        """Merge and scope every record (pure over the records; order independent)."""
        items = [self._merge_item(r) for r in records]
        plan = stable_plan(
            items,
            self.cfg.caps["title_similarity_threshold"],
            self.cfg.cap("merge_fixpoint_cap"),
        )
        by_key = {person_key(r.source_record_id): r for r in records}
        members: dict[str, list[RawPerson]] = {}
        for key, head in plan.canonical_of.items():
            members.setdefault(head, []).append(by_key[key])
        rows: list[Person] = []
        for item in sorted(items, key=lambda i: i.key):
            record = by_key[item.key]
            target = plan.merged_into(item.key)
            if target is None:
                group = members[item.key]
                internal = all(
                    (m.provider_code or "") in self.cfg.internal_only_providers for m in group
                )
            else:
                internal = (record.provider_code or "") in self.cfg.internal_only_providers
            scope_input = ScopeInput(
                merged=target is not None,
                has_url=item.url is not None,
                title=record.title,
                headline=record.headline,
                employer_domain=record.employer_domain,
                is_current=record.is_current,
                search_domain=record.search_domain,
                location=record.location,
            )
            cls = classify(
                scope_input,
                self.cfg,
                inputs.account_domain,
                inputs.regions,
                inputs.allowed_domains,
            )
            if cls.status != "merged" and not looks_like_person(record.name):
                # a title or a quote the source called a person (master prompt v2 section 5)
                cls = Classification(
                    status="excluded",
                    reason="not_a_person_name",
                    rule_id="scope.person_name",
                    evidence="name is a title or a fragment",
                )
            if cls.status != "merged" and tenant_excludes(inputs, record.name, item.url):
                # TN5 pull time: the tenant's exclusion beats every inclusion rule
                cls = Classification(
                    status="excluded",
                    reason="tenant_exclusion",
                    rule_id="tenant.exclusion",
                    evidence="tenant settings",
                )
            cap = self.cfg.cap("max_title_chars")

            def field(value: str | None, cap: int = cap) -> str | None:
                return strip_pii(clean(value, cap)) or None

            rows.append(
                Person(
                    person_key=item.key,
                    source_record_id=record.source_record_id,
                    origin=record.origin,
                    name=field(record.name),
                    title=field(record.title),
                    headline=field(record.headline),
                    profile_url=item.url,
                    profile_url_raw=record.profile_url,
                    employer_domain=item.employer_domain,
                    location=field(record.location),
                    provider_code=record.provider_code,
                    raw_ref=record.raw_ref,
                    call_ref=record.call_ref,
                    lane_searched=record.lane,
                    pass_no=record.pass_no,
                    query=record.query,
                    mention_url=record.mention_url,
                    mention_sentence=field(record.mention_sentence, self.cfg.cap("max_text_chars")),
                    merged_into=target,
                    possible_duplicate=item.key in plan.possible_duplicates,
                    internal_only=internal,
                    classification=cls,
                    fields_dropped=record.fields_dropped,
                )
            )
        return rows, plan

    # ---- step 1 and 4: sources ------------------------------------------------------------

    async def _page(
        self,
        run_id: str,
        spec: SearchSpec,
        stored: dict[str, list[tuple[int, str | None, PersonPage]]],
        w: _Work,
    ) -> SourceOutcome:
        async def call(cursor: str | None) -> PersonPage:
            if spec.pass_no == 0:
                return await self.search.search_association(
                    spec.lane, self.cfg.association_functions, cursor
                )
            return await self.search.search_titles(spec.lane, spec.pass_no, spec.titles, cursor)

        async def save(page_no: int, cursor: str | None, page: PersonPage) -> None:
            await self.repo.save_page(run_id, spec.source_id, page_no, cursor, page)

        cap = self.cfg.cap("max_people_per_run")
        outcome = await page_source(
            spec,
            call,
            save,
            self.cfg,
            stored=stored.get(spec.source_id, []),
            seen_records=w.seen_records,
            seen_people=w.seen_people,
            people_left=lambda: cap - len(w.seen_people),
            out_of_time=self.out_of_time,
        )
        for record in outcome.records:
            w.records.append(
                record.model_copy(
                    update={
                        "lane": spec.lane,
                        "pass_no": spec.pass_no,
                        "query": "; ".join(spec.titles) if spec.titles else "association",
                    }
                )
            )
        w.outcomes.append(outcome)
        w.exits[spec.source_id] = outcome.exit_reason
        return outcome

    async def _stored_pages(
        self, run_id: str
    ) -> dict[str, list[tuple[int, str | None, PersonPage]]]:
        stored: dict[str, list[tuple[int, str | None, PersonPage]]] = {}
        for p in await self.repo.load_pages(run_id):
            stored.setdefault(p.source_id, []).append((p.page_no, p.cursor_in, p.page))
        return stored

    # ---- the whole pass -------------------------------------------------------------------

    async def run_pass(self, run_id: str, inputs: RunInputs) -> PipelineResult:
        """One full pass (the stage checks the store first; see ``people.stage``)."""
        w = _Work()
        stored = await self._stored_pages(run_id)
        specs, not_searched = plan_title_searches(self.cfg, inputs.lanes_off)
        for spec in specs:
            await self._page(run_id, spec, stored, w)
        lanes_on = [lane for lane in self.cfg.lane_ids() if lane not in inputs.lanes_off]
        people, _ = self.build_rows(w.records, inputs)
        association_done: list[str] = []
        gap_exit = "converged"
        # LOOP(people gap passes): cap=max_gap_passes (2); one association search per gap lane,
        # never repeated; exit=converged, no_progress, cap_reached
        for _pass in range(self.cfg.cap("max_gap_passes")):
            gaps = gap_lanes(people, lanes_on)
            if not gaps:
                gap_exit = "converged"
                break
            todo = association_specs(gaps, association_done)
            if not todo:
                gap_exit = "no_progress"
                break
            before = sum(1 for p in people if p.status == "in_scope")
            for spec in todo:
                association_done.append(spec.lane)
                await self._page(run_id, spec, stored, w)
            people, _ = self.build_rows(w.records, inputs)
            if sum(1 for p in people if p.status == "in_scope") <= before:
                gap_exit = "no_progress"
                break
        else:
            gap_exit = "cap_reached" if gap_lanes(people, lanes_on) else "converged"
        w.exits["gap_loop"] = gap_exit
        # a record the source returned without a profile link gets the one profile page the
        # resolver holds for its name (the pages Phase 1 archived); counted, never guessed
        linked = await resolve_unlinked(w.records, self.resolver, self.cfg, self.out_of_time)
        w.records = linked.records
        w.linked = linked.linked
        w.exits["unlinked"] = linked.exit_reason
        mentions = await self._mentions()
        resolved = await resolve_mentions(mentions, self.resolver, self.cfg, self.out_of_time)
        w.exits["mentions"] = resolved.exit_reason
        for record in resolved.records:
            if record.source_record_id not in w.seen_records:
                w.seen_records.add(record.source_record_id)
                w.records.append(record)
        people, plan = self.build_rows(w.records, inputs)
        return await self._after_scope(
            run_id, inputs, people, plan, w, not_searched, specs, resolved
        )

    async def _mentions(self) -> tuple[Any, ...]:
        from people.errors import NotConfigured

        try:
            return tuple(await self.mentions.mentions())
        except NotConfigured:
            return ()

    async def _after_scope(
        self,
        run_id: str,
        inputs: RunInputs,
        people: list[Person],
        plan: MergePlan,
        w: _Work,
        not_searched: dict[str, str],
        specs: Sequence[SearchSpec],
        resolved: Any,
    ) -> PipelineResult:
        targets = list(top_people(people, len(people), self.cfg.level_order))
        stored_profiles = await self.repo.stored_profiles([p.profile_url or "" for p in targets])

        async def save_profiles(batch: Sequence[Profile]) -> None:
            await self.repo.save_profiles(run_id, batch)

        prof = await pull_profiles(
            targets,
            self.profile_port,
            stored_profiles,
            save_profiles,
            self.cfg,
            is_hostile=self.is_hostile,
            out_of_time=self.out_of_time,
        )
        w.exits["profiles"] = prof.exit_reason
        people = [
            p.model_copy(
                update={
                    "profile_state": prof.state[p.person_key][0],
                    "profile_failure": prof.state[p.person_key][1],
                }
            )
            if p.person_key in prof.state
            else p
            for p in people
        ]

        async def save_posts(url: str, batch: Sequence[Post]) -> None:
            await self.repo.save_posts(run_id, url, batch)

        posts_out = await pull_posts(
            people,
            prof.profiles,
            self.posts_port,
            self.costs,
            await self.repo.stored_posts(run_id),
            save_posts,
            self.cfg,
            as_of=inputs.as_of,
            budget_usd=inputs.budget_usd,
            is_hostile=self.is_hostile,
            out_of_time=self.out_of_time,
        )
        w.exits["posts"] = posts_out.exit_reason
        people = [
            p.model_copy(
                update={
                    "posts_requested": p.person_key in posts_out.requested,
                    "activity_unfiltered": p.person_key in posts_out.unfiltered
                    and p.person_key in posts_out.requested,
                }
            )
            for p in people
        ]
        found = await extract_findings(
            people,
            prof.profiles,
            posts_out.posts,
            self.extractor,
            canary=self.canary,
            markers=self.markers,
            max_calls=self.cfg.cap("max_extractor_calls"),
            max_per_person=self.cfg.cap("max_findings_per_person"),
            max_chars=self.cfg.cap("max_text_chars"),
        )
        w.exits["findings"] = found.exit_reason
        profiles = {
            url: pr.model_copy(update={"hostile": True}) if pr.person_key in found.hostile else pr
            for url, pr in prof.profiles.items()
        }
        all_posts = tuple(
            sorted(
                (post for group in posts_out.posts.values() for post in group),
                key=lambda post: (post.person_key, post.post_url),
            )
        )
        counts: PeopleCounts | None = None
        error: str | None = None
        try:
            counts = compute_counts(
                people,
                {
                    p.person_key: profiles[p.profile_url]
                    for p in people
                    if p.profile_url in profiles and p.status == "in_scope"
                },
                all_posts,
                reference_employers=inputs.reference_employers,
                alumni_aliases=self.cfg.alumni_aliases,
                as_of=inputs.as_of,
                buckets=self.cfg.tenure_buckets,
                lanes=self.cfg.lanes,
            )
        except InvariantViolation as exc:
            error = str(exc)
        claims, skipped = title_claims(people)
        claims.extend(finding_claims(found.findings))
        evidence = self._evidence(w, specs, not_searched, prof, posts_out, inputs, people)
        calls = (
            sum(o.live_calls for o in w.outcomes)
            + len(resolved.call_refs)
            + len(resolved.failed_refs)
            + len(prof.call_refs)
            + len(prof.failed_refs)
            + len(posts_out.call_refs)
            + len(posts_out.failed_refs)
        )
        drops = dict(found.dropped)
        drops["title_claim_contact_detail"] = skipped
        drops["mentions_ambiguous"] = resolved.ambiguous
        drops["unlinked_resolved"] = w.linked
        return PipelineResult(
            run_id=run_id,
            as_of=inputs.as_of,
            config_hash=self.cfg.config_hash,
            people=tuple(people),
            profiles=tuple(sorted(profiles.values(), key=lambda p: p.person_key)),
            posts=all_posts,
            findings=tuple(found.findings),
            merge_events=plan.events,
            counts=counts,
            count_sentences=render_count_sentences(counts) if counts else (),
            invariant_error=error,
            exit_reasons=dict(w.exits),
            evidence=evidence,
            claims=tuple(claims),
            lanes_not_searched=not_searched,
            source_codes={},
            drops=drops,
            flags={
                "posts_cost_unknown": posts_out.cost_unknown,
                "profiles_cap_hit": prof.cap_hit,
            },
            findings_hostile=tuple(sorted(found.hostile)),
            calls_made=calls,
        )

    def _evidence(
        self,
        w: _Work,
        specs: Sequence[SearchSpec],
        not_searched: dict[str, str],
        prof: Any,
        posts_out: Any,
        inputs: RunInputs,
        people: Sequence[Person],
    ) -> tuple[RowEvidence, ...]:
        refs = tuple(dict.fromkeys(r for o in w.outcomes for r in (*o.call_refs, *o.failed_refs)))
        searched = len(specs) + sum(1 for o in w.outcomes if o.spec.pass_no == 0)
        reason = None
        if not refs:
            if all(lane in inputs.lanes_off for lane in self.cfg.lane_ids()):
                reason = "lane_not_in_scope"
            else:
                reason = next(
                    (o.unavailable for o in w.outcomes if o.unavailable), "not_available_yet"
                )
                if reason not in ("not_available_yet", "budget_cap", "deadline"):
                    reason = "source_unavailable"
        # a lane switched off is out of scope, not a shortfall; a lane left unsearched for any
        # other reason (no titles configured) is
        shortfall = any(why != "lane_not_in_scope" for why in not_searched.values())
        cap = any(o.cap_hit or not o.complete for o in w.outcomes) or shortfall
        people_ev = RowEvidence(ROW_PEOPLE, refs, max(searched, 1), cap, reason)
        profile_refs = tuple(dict.fromkeys((*prof.call_refs, *prof.failed_refs)))
        in_scope = [p for p in people if p.status == "in_scope"]
        unread = any(p.profile_state not in ("read", "reused") for p in in_scope)
        profiles_ev = RowEvidence(
            ROW_PROFILES,
            profile_refs,
            max(prof.pulled, 1),
            unread,
            None if profile_refs else _not_attempted(why for _, why in prof.state.values()),
        )
        post_refs = tuple(dict.fromkeys((*posts_out.call_refs, *posts_out.failed_refs)))
        posts_ev = RowEvidence(
            ROW_POSTS,
            post_refs,
            max(posts_out.candidates, 1),
            posts_out.cap_hit or posts_out.exit_reason != "converged",
            None if post_refs else _not_attempted(posts_out.failures),
        )
        return (people_ev, profiles_ev, posts_ev)


def _not_attempted(reasons: Any) -> str:
    """The tracker reason for a row with no call: the first failure reason the tracker knows,
    else ``not_planned`` (nothing needed a call)."""
    for reason in reasons:
        if reason in ("not_available_yet", "budget_cap", "deadline"):
            return str(reason)
        if reason:
            return "source_unavailable"
    return "not_planned"


def replayed(result: PipelineResult) -> PipelineResult:
    """The stored result as returned on re-entry: zero calls made this time."""
    return replace(result, calls_made=0, replayed=True)


# ---- serialisation of the run-level record (no provider names, no contact data) ------------


def result_to_json(result: PipelineResult) -> dict[str, object]:
    """Serialisable record."""
    return {
        "run_id": result.run_id,
        "as_of": result.as_of.isoformat(),
        "config_hash": result.config_hash,
        "people": [p.model_dump(mode="json") for p in result.people],
        "profiles": [p.model_dump(mode="json") for p in result.profiles],
        "posts": [p.model_dump(mode="json") for p in result.posts],
        "findings": [f.model_dump(mode="json") for f in result.findings],
        "merge_events": [e.__dict__ for e in result.merge_events],
        "counts": result.counts.model_dump(mode="json") if result.counts else None,
        "count_sentences": list(result.count_sentences),
        "invariant_error": result.invariant_error,
        "exit_reasons": dict(result.exit_reasons),
        "evidence": [e.__dict__ | {"call_refs": list(e.call_refs)} for e in result.evidence],
        "claims": [c.model_dump(mode="json") for c in result.claims],
        "lanes_not_searched": dict(result.lanes_not_searched),
        "source_codes": dict(result.source_codes),
        "drops": dict(result.drops),
        "flags": dict(result.flags),
        "findings_hostile": list(result.findings_hostile),
    }


__all__ = [
    "OWNED_ROWS",
    "Classification",
    "PeopleError",
    "PeoplePipeline",
    "PipelineResult",
    "RowEvidence",
    "RunInputs",
    "SourceUnavailable",
    "person_key",
    "replayed",
    "result_to_json",
    "top_people",
]
