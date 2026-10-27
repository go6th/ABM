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

GUARANTEE(test_pipelines_run_after_the_reading_step_and_report_their_rows)
"""

from __future__ import annotations

import contextlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import ConnectorError, RetryableProviderError
from account_intelligence.connectors.models import FeedArgs, TechDetectionRecord
from account_intelligence.connectors.service import call_operation
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
        from jobs.config import load_jobs_config
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
        pipeline = JobsPipeline(
            jobs_cfg,
            ConnectorPostingSource(deps, host, ctx.blocked_hosts),
            ConnectorPageReader(deps),
            LlmPostingClassifier(deps, "classify_posting", "posting_classifier.v1"),
            LlmPostingClassifier(deps, "second_pass_posting", "posting_second_pass.v1"),
            canary=deps.canary,
            hostile_markers=tuple(gcfg.spans.instruction_markers),
            is_hostile=hostile,
        )
        collector = ClaimCollector([])
        result = await run_jobs(
            str(deps.run_id),
            pipeline,
            PgPostingRepo(deps.txn, deps.tenant_id, deps.account_id),
            RunPayloads(deps),
            TrackerEvidence(deps),
            collector,
            RunJobsInput(
                frozenset((row.run_input or {}).get("lanes_off") or ()), row.created_at.date()
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
        graded = await self.grading.grade_raw(ctx, raws, lane_of)
        counters: Counter[str] = Counter(
            {
                "jobs_postings": len(result.postings),
                "jobs_matched": sum(1 for p in result.postings if p.platforms or p.title_platforms),
                "jobs_read": sum(1 for p in result.postings if p.read),
                "jobs_claims": len(raws),
                "jobs_calls": result.calls_made,
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
        from people.adapters import (
            ConnectorPeopleSearch,
            LedgerCosts,
            LlmPeopleExtractor,
            NoMentions,
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
        repo = PgPeopleRepo(
            deps.txn, deps.tenant_id, deps.account_id, deps.payloads, deps.payload_key
        )
        mentions = NoMentions()
        deadline = deps.deadline
        pipeline = PeoplePipeline(
            cfg,
            ConnectorPeopleSearch(deps, people_operations(), cfg),
            mentions,
            mentions,
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
        except RetryableProviderError:
            raise
        except ConnectorError:
            return PipelineOutcome({"tech_rows": 0}, {"tech": "source_failed"})
        records = [r for r in result.records if isinstance(r, TechDetectionRecord)]
        written = await self._insert_detections(records)
        _log.info("pipelines.tech", extra={"count": len(records), "rows": written})
        return PipelineOutcome(
            {"tech_rows": written, "tech_records": len(records)}, {"tech": "written"}
        )

    async def _insert_detections(self, records: list[TechDetectionRecord]) -> int:
        """Insert the detections once per run (idempotent on technology and dates); register
        each detection's first evidence page as a source so the workbook can cite it."""
        from account_intelligence.db.enums import SourceKind
        from account_intelligence.db.source_ids import get_or_create_source

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
