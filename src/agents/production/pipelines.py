"""``PipelinePort``: the job pipeline and the technology detections, run inside an ABM run.

* ``run_jobs`` is the Workflow v1 ``jobs`` stage body over the same connector adapters
  (``jobs.stage.run_jobs``): the live board searched once per lane keyword on the verified
  careers host, the 12-month feed pulled once (the same ledger key Phase 1 used, so the pull is
  free here), every matched posting read, classified (rules, then the extractor), its platform
  sentences kept, counts computed from rows, the three posting rows reported to the tracker, and
  one templated claim per classified posting and named platform graded through the engine with
  the platform's lane. The careers URL the graph chose from a visited page is recorded first, so
  the board search has a host (``datamodel.careers``: visited, never constructed).
* ``run_people`` is the Workflow v1 ``people`` stage body over the same adapters
  (``people.stage.run_people``): title searches where the active people source offers them,
  the association search per lane on the account's resolved organisation, merge, scope (the
  seniority rule and the exclusions), profiles and posts where a source exists, counts from
  rows, the three people rows reported to the tracker, and the templated claims graded through
  the engine. The graph calls it only under the tenant's people data consent (G8). Switched on
  at the owner's request on 2026-10-24; the active people source decides what it can pull
  (``config/people_sources.yaml``).
* ``write_detections`` turns the technology detections feed (pulled once in Phase 1; replayed
  here) into ``tech_detections`` rows: technology, first seen, last seen, source count and the
  detection's first evidence page, registered as a source so the Tech Stack workbook can cite it.
  A date range is a lead by rule; nothing here is graded or interpreted. Insert-only and
  idempotent per run: a replay writes nothing twice.
* ``write_stack`` turns the organisation tech stack (resolved by domain, then the stack of that
  organisation: business function, category, technology and the postings count the lookup
  reports; both calls were made in Phase 1 and replay free here) into ``tech_stack_items`` rows
  for the Tech Stack workbook's Confirmed stack sheet, in the lookup's own order. Insert-only and
  idempotent per (run, function, category, technology).

GUARANTEE(test_pipelines_run_after_the_reading_step_and_report_their_rows)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.connectors.base import SINGLE_CALL_CEILING_S, Operation
from account_intelligence.connectors.errors import ConnectorError, RetryableProviderError
from account_intelligence.connectors.models import (
    FeedArgs,
    OrganizationRecord,
    OrgResolveArgs,
    OrgTechStackArgs,
    TechDetectionRecord,
    TechStackRecord,
)
from account_intelligence.connectors.service import ConnectorRuntime, call_operation
from account_intelligence.workflow.deps import RunDeps
from agents.ports import PipelineOutcome, RunContext
from obs.logging import logger

_log = logger(__name__)


def people_counters(people: Any, claims: int, calls: int) -> Counter[str]:
    """The people step's run counters: people found and in scope, the people the source returned
    without a usable profile link (the People List's Summary line "no profile link yet"; they are
    never listed), the records whose "name" was a title (never listed), claims and calls."""
    return Counter(
        {
            "people_found": len(people),
            "people_in_scope": sum(1 for p in people if p.status == "in_scope"),
            "people_no_profile_link": sum(1 for p in people if p.status == "unresolved"),
            "people_not_a_person": sum(
                1 for p in people if p.classification.reason == "not_a_person_name"
            ),
            "people_claims": claims,
            "people_calls": calls,
        }
    )


@dataclass
class ClaimCollector:
    """``jobs.ports.GradingPort``: keeps the templated claims for the engine."""

    claims: list[Any]

    async def submit_claims(self, claims: Any) -> None:
        """Keep the claims (graded once the pipeline has persisted its record)."""
        self.claims.extend(claims)


@dataclass(frozen=True)
class RunPipelines:
    """See the module docstring. One instance per run."""

    deps: RunDeps
    grading: Any  # EngineGrading (grade_raw)

    async def run_jobs(self, ctx: RunContext, careers_url: str | None) -> PipelineOutcome:
        """See the module docstring."""
        from account_intelligence.grading.config import load_grading_config
        from account_intelligence.grading.errors import HostileContent
        from account_intelligence.grading.spans import check_hostile
        from jobs.adapters import (
            ConnectorPageReader,
            ConnectorPostingSource,
            LlmPostingClassifier,
            RunJobsInput,
            RunPayloads,
            TrackerEvidence,
        )
        from jobs.claims import claim_id
        from jobs.config import load_jobs_config
        from jobs.incremental import postings_since, previous_run_id
        from jobs.pipeline import JobsPipeline
        from jobs.repo import PgPostingRepo
        from jobs.stage import run_jobs

        deps = self.deps
        gcfg = load_grading_config()

        def hostile(body: str) -> bool:
            """Whether the grading hostile-content check refuses this text."""
            try:
                check_hostile(body, gcfg)
            except HostileContent:
                return True
            return False

        host = await self._careers_host(ctx, careers_url)
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT run_input, created_at FROM account_runs WHERE id = :r"),
                    {"r": deps.run_id},
                )
            ).one()
        jobs_cfg = load_jobs_config()
        run_input = dict(row.run_input or {})
        # the archive copies of the postings pass the page guard (a refused field is withheld)
        from agents.naming import archive_guard

        repo = PgPostingRepo(
            deps.txn,
            deps.tenant_id,
            deps.account_id,
            deps.payloads,
            archive_guard,
            on_archive_failure=lambda _posting: _log.warning(
                "archive.write_failed", extra={"where": "jobs"}
            ),
        )
        # an incremental re-research pulls the delta window only, and only when the previous
        # research's record is at hand: without it nothing would be carried, so the whole window
        # is pulled (jobs.incremental; review 2026-10-29, finding 2)
        wanted = previous_run_id(run_input)
        previous = await repo.load_previous(wanted) if wanted else None
        pipeline = JobsPipeline(
            jobs_cfg,
            ConnectorPostingSource(deps, host, ctx.blocked_hosts),
            ConnectorPageReader(deps),
            LlmPostingClassifier(deps, "classify_posting", "posting_classifier.v1"),
            LlmPostingClassifier(deps, "second_pass_posting", "posting_second_pass.v1"),
            canary=deps.canary,
            hostile_markers=tuple(gcfg.spans.instruction_markers),
            is_hostile=hostile,
            delta_since=postings_since(run_input) if previous is not None else None,
        )
        collector = ClaimCollector([])
        result = await run_jobs(
            str(deps.run_id),
            pipeline,
            repo,
            RunPayloads(deps),
            TrackerEvidence(deps),
            collector,
            RunJobsInput(
                frozenset(run_input.get("lanes_off") or ()),
                row.created_at.date(),
                previous=previous,
            ),
        )
        lane_of: dict[str, str] = {}
        raws = []
        for claim in collector.claims:
            raw = claim.model_dump(mode="json")
            platform = str(raw.get("value") or "")
            with contextlib.suppress(StopIteration):  # a platform outside the lexicon: no lane
                lane_of[str(raw["local_id"])] = jobs_cfg.lane_of(platform)
            raws.append(raw)
        # a carried posting was not fetched in this run, so the engine could not verify its claim
        # against this run's sources: its previous graded fact is recorded again for this run
        # through the engine's write path instead (jobs_carry); the run's other claims are graded
        carried_ids = {
            claim_id(p.posting_key, platform)
            for p in result.postings
            if p.new_since_previous is False
            for platform in p.platforms
        }
        carried_raws = [r for r in raws if str(r.get("local_id")) in carried_ids]
        graded = await self.grading.grade_raw(
            ctx, [r for r in raws if str(r.get("local_id")) not in carried_ids], lane_of
        )
        carried_facts, unmatched = 0, 0
        if carried_raws and result.previous_run_id:
            from account_intelligence.grading.integration import load_run_info
            from agents.production.jobs_carry import carry_job_facts

            copied, missed = await carry_job_facts(
                deps.txn,
                await load_run_info(deps.txn, ctx.tenant_id, ctx.run_id),
                UUID(result.previous_run_id),
                carried_raws,
                self.grading.cfg.content_hash,
            )
            carried_facts, unmatched = len(copied), len(missed)
            graded = {**graded, "graded": int(graded.get("graded", 0)) + carried_facts}
        counters: Counter[str] = Counter(
            {
                "jobs_postings": len(result.postings),
                "jobs_matched": sum(1 for p in result.postings if p.platforms or p.title_platforms),
                "jobs_read": sum(1 for p in result.postings if p.read),
                "jobs_claims": len(raws),
                "jobs_calls": result.calls_made,
                "jobs_claims_without_text": result.claims_skipped_text,
                "jobs_description_less": result.description_less,
                "jobs_carried_facts": carried_facts,
                "jobs_carried_claims_without_fact": unmatched,
            }
        )
        counters.update({f"jobs_grade_{k}": v for k, v in graded.items()})
        exits = ",".join(f"{k}={v}" for k, v in sorted(result.exit_reasons.items()))
        _log.info(
            "pipelines.jobs",
            extra={
                "count": len(result.postings),
                "rows": counters["jobs_read"],
                "matched": counters["jobs_matched"],
                "read": sum(1 for p in result.postings if p.escalation != "none"),
                "dropped": len(raws) - int(graded.get("graded", 0)),
                "exits": exits[:200],
            },
        )
        return PipelineOutcome(
            dict(counters), {f"jobs_{k}": v for k, v in result.exit_reasons.items()}
        )

    async def run_people(self, ctx: RunContext) -> PipelineOutcome:
        """See the module docstring."""
        import time

        from account_intelligence.workflow.handlers import people_operations
        from account_intelligence.workflow.stages.people import _is_hostile
        from agents.production.people_links import ArchiveResolver, FactMentions
        from people.adapters import (
            ConnectorPeopleSearch,
            LedgerCosts,
            LlmPeopleExtractor,
            PeopleTrackerEvidence,
            RunPeopleInput,
            SourceRegistry,
            UnavailablePosts,
            UnavailableProfiles,
        )
        from people.config import load_people_config
        from people.pipeline import PeoplePipeline
        from people.repo import PgPeopleRepo
        from people.stage import run_people

        deps = self.deps
        cfg = load_people_config()
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT run_input, created_at, budget_usd FROM account_runs WHERE id = :r"
                    ),
                    {"r": deps.run_id},
                )
            ).one()
        share = next((st.budget_share for st in deps.spec.stages if st.id == "people"), None)
        budget = (
            float(row.budget_usd) * float(share)
            if row.budget_usd is not None and share is not None
            else None
        )
        hostile, markers = _is_hostile()
        from agents.naming import archive_guard

        repo = PgPeopleRepo(
            deps.txn,
            deps.tenant_id,
            deps.account_id,
            deps.payloads,
            deps.payload_key,
            guard=archive_guard,
            on_archive_failure=lambda error: _log.warning(
                "archive.write_failed",
                extra={"where": "people", "error": type(error).__name__},
            ),
        )
        # people named in the graded facts, and the archived profile pages of the research's
        # own searches, so a named executive or a person returned without a link gets the
        # profile page the research already holds (no paid call; link archive 2026-10-27)
        from agents.config import skill_config

        deadline = deps.deadline
        pipeline = PeoplePipeline(
            cfg,
            ConnectorPeopleSearch(deps, people_operations(), cfg),
            FactMentions(deps),
            ArchiveResolver(deps, ctx.account_name, tuple(skill_config().scope.name_stopwords)),
            # Full profile pulls cost credits per person and are NOT wired here: the People List's
            # "Former SI and consulting employers" cell stays empty (no profile read, no claim).
            # Wiring a profile pull is the owner's decision (people.profiles.to_profile already
            # reads dated ``employments`` and people.si_firms prints them).
            UnavailableProfiles(),
            UnavailablePosts(),
            LedgerCosts(deps),
            LlmPeopleExtractor(deps),
            repo,
            canary=deps.canary,
            hostile_markers=markers,
            is_hostile=hostile,
            out_of_time=lambda: deadline is not None and time.monotonic() >= deadline,
        )
        collector = ClaimCollector([])
        result = await run_people(
            str(deps.run_id),
            deps.domain,
            pipeline,
            repo,
            SourceRegistry(deps),
            PeopleTrackerEvidence(deps),
            collector,
            RunPeopleInput(
                dict(row.run_input or {}), row.created_at.date(), budget, ctx.allowed_domains
            ),
        )
        raws = [c.model_dump(mode="json") for c in collector.claims]
        graded = await self.grading.grade_raw(ctx, raws, {})
        counters = people_counters(result.people, len(raws), result.calls_made)
        counters.update({f"people_grade_{k}": v for k, v in graded.items()})
        _log.info(
            "pipelines.people",
            extra={
                "count": len(result.people),
                "rows": counters["people_in_scope"],
                "withheld": counters["people_no_profile_link"],
                "dropped": len(raws) - int(graded.get("graded", 0)),
            },
        )
        return PipelineOutcome(
            dict(counters), {f"people_{k}": v for k, v in sorted(result.exit_reasons.items())}
        )

    async def _careers_host(self, ctx: RunContext, chosen: str | None) -> str | None:
        """The verified careers host: the account's stored one, else the page the graph chose
        among the pages it visited this run (recorded with the ledger key of its read)."""
        from datamodel.careers import ConstructedUrl, Visit, record_visit

        deps = self.deps
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT careers_url, careers_url_verified_at FROM accounts WHERE id = :a"),
                    {"a": deps.account_id},
                )
            ).first()
            stored = row.careers_url if row and row.careers_url_verified_at else None
            if stored is None and chosen:
                ref = (
                    await s.execute(
                        text(
                            "SELECT args_hash FROM tool_calls WHERE run_id = :r"
                            " AND response->'args'->>'url' = :u ORDER BY created_at LIMIT 1"
                        ),
                        {"r": deps.run_id, "u": chosen},
                    )
                ).scalar_one_or_none()
                if ref:
                    try:
                        await record_visit(
                            s,
                            account_id=deps.account_id,
                            domain=deps.domain,
                            visit=Visit(chosen, str(ref), datetime.now(UTC)),
                        )
                        stored = chosen
                    except ConstructedUrl:
                        stored = None
        return (urlsplit(stored).hostname or "").lower() if stored else None

    async def write_detections(self, ctx: RunContext) -> PipelineOutcome:
        """See the module docstring."""
        deps = self.deps
        if deps.connectors is None:
            return PipelineOutcome({"tech_rows": 0}, {"tech": "not_available_yet"})
        from agents.config import skill_config
        from agents.production.provider import feed_since

        async with deps.txn(deps.tenant_id) as s:
            created: datetime | None = (
                await s.execute(
                    text("SELECT created_at FROM account_runs WHERE id = :r"), {"r": deps.run_id}
                )
            ).scalar_one()
        anchor = created.date().replace(day=1) if created else date.today().replace(day=1)
        since = feed_since(Operation.TECH_DETECTIONS, anchor, skill_config().scope)
        try:
            result = await call_operation(
                Operation.TECH_DETECTIONS,
                FeedArgs(domain=deps.domain, since=since),
                deps.call_context(),
                deps.connectors,
            )
        except RetryableProviderError as error:
            # the node RetryPolicy owns retries: handed up below its budget, a stated source
            # failure above it (the same rule as the jobs and people adapters)
            if (error.attempt or 1) < deps.retry_max_attempts:
                raise
            return PipelineOutcome({"tech_rows": 0}, {"tech": "source_failed"})
        except ConnectorError:
            return PipelineOutcome({"tech_rows": 0}, {"tech": "source_failed"})
        records = [r for r in result.records if isinstance(r, TechDetectionRecord)]
        written = await self._insert_detections(records)
        _log.info("pipelines.tech", extra={"count": len(records), "rows": written})
        return PipelineOutcome(
            {"tech_rows": written, "tech_records": len(records)},
            {"tech": "written"},
            tuple(result.call_refs),
        )

    async def _insert_detections(self, records: list[TechDetectionRecord]) -> int:
        """Insert the detections once per run (idempotent on technology and dates); register
        each detection's first evidence page as a source so the workbook can cite it."""
        from account_intelligence.db.enums import SourceKind
        from account_intelligence.db.source_ids import get_or_create_source
        from agents.naming import archive_guard

        deps = self.deps
        written = 0
        async with deps.txn(deps.tenant_id) as s:
            existing = {
                (r.technology, r.first_seen_at, r.last_seen_at)
                for r in (
                    await s.execute(
                        text(
                            "SELECT technology, first_seen_at, last_seen_at FROM tech_detections"
                            " WHERE run_id = :r AND account_id = :a"
                        ),
                        {"r": deps.run_id, "a": deps.account_id},
                    )
                ).all()
            }
            for rec in records:
                name = (rec.technology_name or "").strip()
                if not name:
                    continue
                key = (name, rec.first_seen_at[:10], rec.last_seen_at[:10])
                if key in existing:
                    continue
                existing.add(key)
                url = next(
                    (u for u in rec.evidence_urls if u.startswith(("http://", "https://"))), None
                )
                source_ref = url
                if url:

                    def register(sync: Any, page: str = url) -> None:
                        get_or_create_source(
                            sync,
                            account_id=deps.account_id,
                            account_run_id=deps.run_id,
                            url=page,
                            kind=SourceKind.SECONDARY,
                        )

                    try:
                        await s.run_sync(register)
                    except Exception:  # noqa: BLE001 - an unregistrable page keeps its date range
                        source_ref = None
                    # the detection as the feed reported it is the archived copy of its page; an
                    # archive failure never fails the step (pipeline review 2026-10-29, 12)
                    try:
                        when = datetime.now(UTC)
                        record = AR.detection_record(
                            url,
                            technology=name[:200],
                            first_seen=key[1],
                            last_seen=key[2],
                            sources=int(rec.source_count),
                            captured_at=when,
                            guard=archive_guard,
                        )
                        body = AR.snapshot_bytes(record)
                        ref = await deps.payloads.put(
                            LA.archive_key(
                                deps.tenant_id, deps.account_id, AR.identity_bytes(record)
                            ),
                            body,
                        )
                        async with s.begin_nested():
                            await LA.record(
                                s,
                                deps.tenant_id,
                                deps.account_id,
                                deps.run_id,
                                [
                                    LA.ArchiveEntry(
                                        url=url,
                                        kind="detection",
                                        payload_ref=ref,
                                        archived_at=when,
                                        title=name[:200],
                                        source_ref=rec.provenance.raw_ref,
                                        meta=AR.index_meta(record),
                                    )
                                ],
                            )
                    except Exception as error:  # noqa: BLE001 - see above
                        _log.warning(
                            "archive.write_failed",
                            extra={"where": "detection", "error": type(error).__name__},
                        )
                await s.execute(
                    text(
                        "INSERT INTO tech_detections (tenant_id, account_id, run_id, technology,"
                        " first_seen_at, last_seen_at, source_count, source_ref)"
                        " VALUES (:t, :a, :r, :n, :f, :l, :c, :s)"
                    ),
                    {
                        "t": deps.tenant_id,
                        "a": deps.account_id,
                        "r": deps.run_id,
                        "n": name[:200],
                        "f": key[1],
                        "l": key[2],
                        "c": int(rec.source_count),
                        "s": source_ref or rec.provenance.raw_ref,
                    },
                )
                written += 1
        return written

    async def write_stack(self, ctx: RunContext) -> PipelineOutcome:
        """See the module docstring. A domain the lookup does not resolve writes nothing."""
        deps = self.deps
        if deps.connectors is None:
            return PipelineOutcome({"stack_rows": 0}, {"stack": "not_available_yet"})
        if not await self._stack_was_pulled():
            # never a new paid call from here: the lookup is made in Phase 1 (the people step's
            # association rows); a run that did not make it has no stack to store
            return PipelineOutcome({"stack_rows": 0}, {"stack": "not_pulled"})
        try:
            # both lookups replay from the ledger when Phase 1 made them; one that never answers
            # is waited for a bounded time and then named a failed source (F9)
            async with asyncio.timeout(SINGLE_CALL_CEILING_S):
                result = await self._stack_lookups(deps.connectors)
        except TimeoutError:
            return PipelineOutcome({"stack_rows": 0}, {"stack": "source_failed"})
        except RetryableProviderError as error:
            if (error.attempt or 1) < deps.retry_max_attempts:
                raise
            return PipelineOutcome({"stack_rows": 0}, {"stack": "source_failed"})
        except ConnectorError:
            return PipelineOutcome({"stack_rows": 0}, {"stack": "source_failed"})
        if result is None:
            return PipelineOutcome({"stack_rows": 0}, {"stack": "not_matched"})
        records = [r for r in result.records if isinstance(r, TechStackRecord)]
        written, skipped = await self._insert_stack(records)
        _log.info(
            "pipelines.stack", extra={"count": len(records), "rows": written, "skipped": skipped}
        )
        return PipelineOutcome(
            {"stack_rows": written, "stack_records": len(records), "stack_skipped_empty": skipped},
            {"stack": "written"},
        )

    async def _stack_lookups(self, runtime: ConnectorRuntime) -> Any:
        """The organisation of the domain, then its tech stack; None when no organisation with an
        id matches."""
        deps = self.deps
        cc = deps.call_context()
        org = await call_operation(
            Operation.ORG_RESOLVE, OrgResolveArgs(domain=deps.domain), cc, runtime
        )
        # the same organisation Phase 1 looked up: the first matched record that has an id
        # (a matched record without one is passed over, not a reason to find no stack)
        org_id = next(
            (
                r.organization_id
                for r in org.records
                if isinstance(r, OrganizationRecord) and r.matched and r.organization_id
            ),
            None,
        )
        if org_id is None:
            return None
        return await call_operation(
            Operation.ORG_TECH_STACK, OrgTechStackArgs(organization_id=org_id), cc, runtime
        )

    async def _stack_was_pulled(self) -> bool:
        """True when this run, or the research it builds on, already made the tech stack lookup
        (so the calls below replay and cost nothing)."""
        deps = self.deps
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT 1 FROM tool_calls WHERE operation = 'org_tech_stack'"
                        " AND run_id IN (:r, (SELECT parent_run_id FROM account_runs"
                        " WHERE id = :r)) LIMIT 1"
                    ),
                    {"r": deps.run_id},
                )
            ).first()
        return row is not None

    async def _insert_stack(self, records: list[TechStackRecord]) -> tuple[int, int]:
        """Insert the stack once per run: one row per (business function, category, technology),
        the first of a repeat kept, ``position`` the item's place in the lookup's order. Returns
        ``(rows written, items skipped for having no technology name)``: an item with no name is
        never silently dropped, it is counted. Every row names the account's owner (as
        ``link_archive`` does): the writer is a worker in system scope, which has no user of its
        own (security review 2026-10-29, F14)."""
        deps = self.deps
        written = 0
        skipped = 0
        async with deps.txn(deps.tenant_id) as s:
            existing = {
                (r.business_function, r.category, r.technology)
                for r in (
                    await s.execute(
                        text(
                            "SELECT business_function, category, technology FROM tech_stack_items"
                            " WHERE run_id = :r AND account_id = :a"
                        ),
                        {"r": deps.run_id, "a": deps.account_id},
                    )
                ).all()
            }
            for position, rec in enumerate(records):
                key = (
                    (rec.business_function or "").strip()[:200],
                    (rec.category_name or "").strip()[:200],
                    (rec.technology_name or "").strip()[:200],
                )
                if not key[2]:
                    skipped += 1
                    continue
                if key in existing:
                    continue
                existing.add(key)
                await s.execute(
                    text(
                        "INSERT INTO tech_stack_items (tenant_id, owner_user_id, account_id,"
                        " run_id, business_function, category, technology, postings, position,"
                        " source_ref) VALUES (:t, coalesce((SELECT a.owner_user_id FROM accounts a"
                        " WHERE a.tenant_id = :t AND a.id = :a), app_user()), :a, :r, :f, :c, :n,"
                        " :p, :o, :s)"
                    ),
                    {
                        "t": deps.tenant_id,
                        "a": deps.account_id,
                        "r": deps.run_id,
                        "f": key[0],
                        "c": key[1],
                        "n": key[2],
                        "p": max(0, int(rec.job_post_used_count)),
                        "o": position,
                        "s": rec.provenance.raw_ref,
                    },
                )
                written += 1
        return written, skipped
