"""The eight-step run as one LangGraph graph with three entry slices (D-102, G14 to G17).

::

    setup -> cap_approval -> pull <-> spend_gate          (Phase 1 slice: modes full, pull)
          -> [pull: finish]
          -> load_manifest -> interpret -> pipelines -> person_searches -> sense_check
                                                                 (Phase 2: modes full, build)
             -> [sense_approval] -> [targeted_pulls -> sense_check (second, record only)]
             -> distill -> build -> gate -> deliver -> finish

* ``full`` runs the Phase 1 slice and then the Phase 2 slice on its own manifest; ``build`` starts
  the Phase 2 slice on its parent's manifest; ``pull`` stops after Phase 1. The Phase 2 slice
  reads the manifest from the store in every mode, so ``full`` is exactly ``pull`` then ``build``.
* Phase 1 stores payloads and a manifest and nothing else: no grading, tracker verdict, files or
  delivery (G5, G15).
* ``pipelines`` (2026-10-24) runs the skill's code steps after the reading step: the job pipeline
  (every posting of the last 12 months read, classified and counted; one graded claim per
  classified posting and platform) and the technology detections as dated rows. Rows a pipeline
  owns are not read by the extractor (``pull_plan.yaml`` ``pipeline``).
* ``person_searches`` (2026-10-24) runs the skill's step 9: the speaking, podcast and video rows
  are searched by the names of the executives the reading step found (code-built queries from a
  fixed template, at most ten people), the pages read, the findings graded.
* The build node requires a stored sense check over the same manifest and facts (G6).
* Spending beyond the approved cap pauses through an interrupt in attended runs and is recorded in
  unattended ones (G7, D-96); the approval points are ``cap_approval``, ``spend_gate``,
  ``sense_approval`` and ``token_gate``.
* ``token_gate``: a model node whose next paid call would pass the run's token guard (a multiple
  of its pre-run token estimate, ``connectors.spend_guard.check_tokens``; on while the research
  caps are off) pauses every run, attended or not, and asks; continuing re-runs that node (its
  finished replies replay free), declining ends the run with what it found.
* ``finish`` is the only node that settles, and the wallet settles once per run.
* The state holds ids, counters and small references only.
* Interrupt values follow the runner's convention: ``awaiting: approval`` for the first cap
  confirmation, ``awaiting: cap`` (with a ``reason``) for spend and sense-check pauses, so the
  runner applies the credit manager's pause timeout.

GUARANTEE(test_full_equals_pull_then_build)
GUARANTEE(test_pull_mode_produces_a_manifest_and_nothing_else)
GUARANTEE(test_build_never_runs_phase_one)
GUARANTEE(test_sense_check_always_precedes_build)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Coroutine, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, TypeVar, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from account_intelligence.connectors.spend_guard import TokenGuardReached
from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured
from account_intelligence.workflow.ports import BuildIncomplete
from account_intelligence.workflow.state import merge_maps, sum_counters
from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.distill.agent import distill
from agents.entity import EntityLock, accept
from agents.errors import BudgetExceeded, GuardViolation, NoPulledData, StalePullsNeedDecision
from agents.extraction import ExtractionResult, extract, groups, value_key
from agents.guards import (
    ACCOUNT_KEYED_NEEDS,
    OFF_TARGET_EXEMPT_ROWS,
    STATED_GAPS,
    consent_decision,
    coverage_verdict,
    facts_digest,
    internal_history_allowed,
    lead_check,
    on_target,
    require_sense_check,
    spend_route,
)
from agents.guards import (
    TERMINAL as ROW_TERMINAL,
)
from agents.manifest import ManifestRow, PullManifest, check_build_precondition
from agents.naming import assert_client_clean
from agents.people_names import PERSON_ROWS, people_named, person_queries
from agents.ports import (
    AgentModel,
    BuilderPort,
    CapPause,
    Clock,
    ConsentStore,
    CreditPort,
    DeliveryPort,
    GatePort,
    GradingCandidate,
    GradingPort,
    InternalSourcePort,
    ManifestStore,
    NotConfigured,
    PayloadPort,
    PipelinePort,
    ProviderFailed,
    ProviderPort,
    RunContext,
    RunRecordStore,
    SenseCheckRecord,
    SettingsPort,
    TrackerPort,
    WalletPort,
)
from agents.pull.agent import CREDIT_LIMIT, PullAgent, SpendPause
from agents.schemas import ExtractionInput, PayloadView, SenseCheckInput, StoredFact
from agents.sense_check.agent import sense_check
from crosscut.fanout import fan_out, fan_out_all
from llm.errors import (
    AdapterNotConfigured,
    AuthFailed,
    ContentRefused,
    ContextTooLong,
    ModelUnavailable,
    ProviderCreditsExhausted,
)
from obs.logging import logger

_log = logger(__name__)
T = TypeVar("T")
MAX_SPEND_PAUSES = 3
# one planned row's pull may take this long (a third of a 1650 s slice); past it the row is a gap
PULL_ROW_CEILING_S = 540
STEPS: tuple[Literal["pass1", "pass2", "people"], ...] = ("pass1", "pass2", "people")
# extraction calls in flight at once per step (each call is independent; see _interpret). Each is
# a one-request Message Batch that takes minutes on the provider's side, so the node's duration is
# calls / concurrency x the batch time, not compute. Tolmar (staging 2026-10-21) needed 121
# calls: at 8 at a time and ~285 s a batch that is ~75 minutes, so no 20 minute slice could hold
# the reading step and it took five slices. At 32 it is ~20 minutes. Polling stays inside the
# provider's batch request rate (config/models.yaml batch.poll_seconds). In `fast` speed mode
# (models.yaml batch.mode) the calls are standard requests that answer in seconds, and the three
# steps run side by side on this one gate.
EXTRACT_CONCURRENCY = 32
# a run that graded no fact has nothing to build: it ends finished with this reason instead of
# the builder's missing_content (audit 2026-10-20: five McKesson refreshes ended "stopped before
# finishing" and offered a Resume that re-paid the same extraction for the same empty result)
NO_NEW_FINDINGS = "no_new_findings"
# the gate held back some client files and the rest were delivered (documents.gate FILE_CHECKS)
FILES_WITHHELD = "files_withheld"
STEP_ORDER: dict[str, int] = {step: index for index, step in enumerate(STEPS)}


@dataclass(frozen=True)
class AbmDeps:
    """Everything the graph calls. Built once per process; holds ports, never tenant data."""

    cfg: SkillConfig
    registry: Mapping[str, AgentContract]
    clock: Clock
    model: AgentModel
    provider: ProviderPort
    payloads: PayloadPort
    manifests: ManifestStore
    consent: ConsentStore
    settings: SettingsPort
    grading: GradingPort
    tracker: TrackerPort
    credit: CreditPort
    records: RunRecordStore
    builder: BuilderPort
    gate: GatePort
    delivery: DeliveryPort
    wallet: WalletPort
    internal: InternalSourcePort | None = None
    free_operations: frozenset[str] = frozenset()
    # the code pipelines after the reading step (None: the node records "not available yet")
    pipelines: PipelinePort | None = None


class AbmState(TypedDict, total=False):
    """Checkpoint state: ids, flags, counters and small references (no text)."""

    tenant_id: str
    run_id: str
    mode: str
    parent_run_id: str | None
    account_key: str
    account_name: str
    domain: str
    attended: bool
    config_version: str
    acknowledged_stale: list[str]
    cap_request_micros: int
    cap_declined: bool
    spend_declined: bool
    spend_pauses: int
    pending_spend_row: str | None
    # the model node the token guard paused (token_gate re-runs it once the user continues)
    token_pause_node: str | None
    token_resume_node: str | None
    token_pauses: int
    manifest_digest: str
    sense_pass: int
    to_run: list[dict[str, str]]
    awaiting: list[dict[str, str]]
    client_files: list[str]
    internal_files: list[str]
    outcome: str
    scope: str
    # a research job's scope (refresh "jobs", "people", "topic"): the rows it pulls; None is every
    # row. ``topic`` is the user's own words for a topic refresh (a search phrase, never an order)
    scope_rows: list[str] | None
    topic: str | None
    # New research's optional goal (the user's words, data never an order): adds one search to
    # each ``goal_rows`` row on top of its own queries and steers the distill summary
    goal: str | None
    goal_rows: list[str]
    error_code: str | None
    missing_rows: list[str]
    # client file kinds the gate held back (the others were delivered)
    withheld_files: list[str]
    careers_url: str | None
    # the entity lock (master prompt v2 section 3), ``EntityLock.as_dict()``: set by the
    # entity_lock node before any pull; every later node reads the account's domains from it
    entity_lock: dict[str, Any] | None
    settled: bool
    counters: Annotated[dict[str, int], sum_counters]
    exits: Annotated[dict[str, str], merge_maps]


def context(state: AbmState) -> RunContext:
    """The run context from the trusted run row fields the runner put in the state."""
    lock = state.get("entity_lock") or {}
    own = tuple(str(d) for d in lock.get("allowed_domains") or () if str(d) != state["domain"])
    looks = lock.get("blocked_lookalikes") or ()
    blocked = tuple(str(x.get("host")) for x in looks if x.get("host"))
    return RunContext(
        tenant_id=UUID(state["tenant_id"]),
        run_id=UUID(state["run_id"]),
        account_key=state["account_key"],
        account_name=state["account_name"],
        domain=state["domain"],
        attended=bool(state.get("attended")),
        config_version=state["config_version"],
        allowed_domains=own,
        blocked_hosts=blocked,
    )


def _lock(state: AbmState) -> EntityLock | None:
    raw = state.get("entity_lock")
    return EntityLock.from_dict(raw) if raw else None


def _fail(code: str, **extra: Any) -> dict[str, Any]:
    return {"outcome": "failed", "error_code": code, **extra}


def may_state(grade: str) -> bool:
    """Whether a client file may state a fact of this product grade (the grading policy:
    Confirmed only; an unknown grade is never stated)."""
    from account_intelligence.grading.config import load_grading_config

    return bool(load_grading_config().may_state.get(str(grade).lower(), False))  # type: ignore[call-overload]


# Model failures the node RetryPolicy must not retry and no fallback can serve. The run keeps
# everything it pulled and graded and ends partial with the reason (never FAILED with a refund of
# the pulls' worth of research): observed 2026-10-12 as runs that died after $50 of pulls on a
# provider credit-balance 400 and on a missing model key.
MODEL_FAILURES: tuple[tuple[type[BaseException], str], ...] = (
    (ProviderCreditsExhausted, "model_credits_exhausted"),
    (AuthFailed, "model_auth_failed"),
    (ModelUnavailable, "model_unavailable"),
    (AdapterNotConfigured, "model_not_configured"),
    (PlatformNotConfigured, "model_not_configured"),
    (ContentRefused, "model_refused"),
)


def model_failure_code(error: BaseException) -> str | None:
    """The partial-outcome code for a terminal model failure, or None for any other error."""
    for cls, code in MODEL_FAILURES:
        if isinstance(error, cls):
            return code
    return None


async def _all_or_none(coros: Iterable[Coroutine[Any, Any, T]]) -> list[T]:
    """Run the calls side by side and return their results in order. The first failure cancels
    the others (none keeps paying for a model call, or saving facts, for a node that has already
    failed) and is raised as itself, so the node's retry policy and its model-failure outcome
    still see the real error."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]
    except BaseExceptionGroup as failed:
        raise failed.exceptions[0]  # noqa: B904 - the original error, its own cause kept
    return [t.result() for t in tasks]


def _in_scope(state: AbmState, cfg: SkillConfig) -> set[str]:
    """The rows this run pulls: its job's scope, or the whole plan."""
    rows = state.get("scope_rows")
    return set(cfg.plan) if not rows else set(rows) & set(cfg.plan)


# the skill's spend priority inside the cap (phase1-pull.md): people completeness first, then the
# structured feeds, stack and teams, then releases, media and searches. Rows used to be pulled in
# step and name order, so a capped run spent its budget on the first pass 1 searches and never
# reached jobs, technology or people (audit 2026-10-21).
SPEND_PRIORITY: Mapping[str, int] = {"people": 0, "feeds": 1, "searches": 2}


def _spend_class(row: Any) -> str:
    if row.step == "people" or row.need in ("people_by_title", "people_by_technology"):
        return "people"
    return "feeds" if row.need in ACCOUNT_KEYED_NEEDS else "searches"


def _plan_order(cfg: SkillConfig) -> list[str]:
    return sorted(
        cfg.plan,
        key=lambda k: (
            SPEND_PRIORITY[_spend_class(cfg.plan[k])],
            STEP_ORDER[cfg.plan[k].step],
            k,
        ),
    )


RETRIED_NODES = frozenset(
    {
        "setup",
        "entity_lock",
        "pull",
        "interpret",
        "pipelines",
        "person_searches",
        "sense_check",
        "targeted_pulls",
        "distill",
    }
)


def build_abm_graph(deps: AbmDeps, retry_policy: Any = None) -> StateGraph[AbmState]:
    """The uncompiled graph; compile with the run checkpointer (see the module docstring)."""
    cfg, reg = deps.cfg, deps.registry

    def meter(name: str) -> BudgetMeter:
        return BudgetMeter(reg[name], started_at=deps.clock.now())

    # Step 1 ------------------------------------------------------------------------------------
    async def setup(state: AbmState) -> dict[str, Any]:
        """Step 1: per-client settings must be valid before anything runs. A pursuing client is
        optional: without one the account is researched by its name alone."""
        ctx = context(state)
        settings = await deps.settings.get(ctx.tenant_id)
        if set(settings.lanes) - set(cfg.lanes):
            return _fail("invalid_setup")
        return {"scope": state["mode"], "outcome": "running", "exits": {"setup": "validated"}}

    async def entity_lock(state: AbmState) -> dict[str, Any]:
        """Master prompt v2 section 3, the blocking gate before any pull: the account is locked
        from its own home page and one brand search. A home page that was read and does not name
        the account ends the run ``entity_mismatch`` before a credit is spent on it (2026-10-24:
        a run researched vizent.co, a 3D studio, for an account named Vizient). A build run
        reuses the lock its parent stored."""
        if state.get("outcome") == "failed":
            return {}
        ctx = context(state)
        stored = await deps.records.entity_lock(ctx) if state["mode"] == "build" else None
        if stored:
            lock = EntityLock.from_dict(dict(stored))
        else:
            lock = await deps.provider.entity_lock(ctx)
            await deps.records.save_entity_lock(ctx, lock.as_dict())
        outcome = "confirmed" if lock.confirmed else ("mismatch" if lock.mismatch else "unverified")
        _log.info(
            "entity.locked",
            extra={
                "outcome": outcome,
                "count": len(lock.allowed_domains),
                "dropped": len(lock.blocked_lookalikes),
            },
        )
        if outcome == "mismatch":
            return _fail(
                "entity_mismatch", entity_lock=lock.as_dict(), exits={"entity_lock": outcome}
            )
        return {"entity_lock": lock.as_dict(), "exits": {"entity_lock": outcome}}

    async def cap_approval(state: AbmState) -> dict[str, Any]:
        """G7 / S16: the cap request is confirmed before any paid call of Phase 1."""
        if state.get("outcome") == "failed" or state["mode"] == "build":
            return {}
        if await deps.credit.propose_cap(context(state)):
            return {"exits": {"cap_approval": "approved"}}
        cap = await deps.credit.cap_state(context(state))
        if not cap.cap_required or cap.cap_version is not None:
            return {}
        if not state.get("attended"):
            return {"exits": {"cap_approval": "unattended_no_cap"}}
        decision = interrupt(
            {
                "awaiting": "approval",
                "reason": "cap",
                "cap_request_micros": state.get("cap_request_micros", 0),
            }
        )
        return {"cap_declined": not bool(decision), "exits": {"cap_approval": "decided"}}

    # Phase 1 -----------------------------------------------------------------------------------
    async def pull(state: AbmState) -> dict[str, Any]:
        """Phase 1: pull every planned row through the ledger and store the manifest (G5, G15)."""
        ctx = context(state)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        problem = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version)
        settings = await deps.settings.get(ctx.tenant_id)
        internal = internal_history_allowed(settings.internal_history, cfg.internal_history_default)
        agent = PullAgent(reg["pull"], cfg, deps.provider, deps.payloads, deps.clock, deps.internal)
        agent.topic = state.get("topic")
        agent.goal = state.get("goal")
        agent.goal_rows = frozenset(state.get("goal_rows") or ())
        in_scope = _in_scope(state, cfg)
        agent.record_refusals = bool(
            state.get("cap_declined")
            or state.get("spend_declined")
            or state.get("spend_pauses", 0) >= MAX_SPEND_PAUSES
        )
        m = meter("pull")
        # the rows that are not pulled are decided here, in plan order; the rest are pulled side
        # by side (``fan_out``) and put back into plan order, so the manifest is the one the
        # one-at-a-time loop made (a pull of 33 rows took minutes of idle waiting on the provider)
        fixed: dict[str, ManifestRow] = {}
        todo: list[str] = []
        for key in _plan_order(cfg):
            if key not in in_scope:
                # outside this job's scope: reported "not planned for this run", never silent
                fixed[key] = agent.blocked(cfg.plan[key], "not_planned")
            elif cfg.plan[key].pipeline == "people" and deps.pipelines is not None:
                # the people pipeline pulls, merges and records the senior people list itself
                # after the reading step, under the tenant's consent (switched on 2026-10-24);
                # Phase 1 does not pay for a pull the pipeline would repeat
                fixed[key] = agent.blocked(cfg.plan[key], "owned_by_pipeline")
            elif (
                cfg.plan[key].step == "people"
                and problem is None
                and not getattr(deps.grading, "records_people", True)
                and deps.pipelines is None
            ):
                # no pipeline to record people: the pull is not paid for only to be thrown away
                fixed[key] = agent.blocked(cfg.plan[key], "people_not_recorded")
            else:
                todo.append(key)
        pauses: dict[str, SpendPause] = {}

        async def pull_one(key: str) -> ManifestRow:
            """One row pulled; a spend pause is recorded and stops new rows from starting."""
            try:
                async with asyncio.timeout(PULL_ROW_CEILING_S):
                    return await agent.pull_row(
                        ctx, cfg.plan[key], m, consent_problem=problem, internal_history=internal
                    )
            except TimeoutError:
                # one provider call that never answers must not hold the whole node past the
                # slice (no checkpoint, three empty slices, run ended PARTIAL on 2026-10-27/28).
                # A row a code pipeline owns (jobs, tech) is handed to it: the pipeline re-issues
                # the same ledger keys, replays the pages fetched so far and finishes the pull
                # (the 12-month postings feed of a large account paginates for 20 minutes). Any
                # other row is a named gap; every other row is kept.
                handed = cfg.plan[key].pipeline in ("jobs", "tech") and deps.pipelines is not None
                _log.log(
                    logging.INFO if handed else logging.WARNING,
                    "pull.row_slow",
                    extra={
                        "stage": key[:60],
                        "waited_s": PULL_ROW_CEILING_S,
                        "outcome": "handed_to_pipeline" if handed else "gap",
                    },
                )
                return agent.blocked(
                    cfg.plan[key], "owned_by_pipeline" if handed else "provider_slow"
                )
            except SpendPause as pause:
                pauses[key] = pause  # no new row starts; the rows in flight finish
                return agent.blocked(cfg.plan[key], "spend_pause")
            except BudgetExceeded:
                return agent.blocked(cfg.plan[key], "agent_budget_exceeded")

        pulled_rows = await fan_out(todo, pull_one, stop=lambda: bool(pauses))
        if pauses:
            first = next(k for k in todo if k in pauses)
            return {"pending_spend_row": pauses[first].row_key}
        for key, row in zip(todo, pulled_rows, strict=True):
            fixed[key] = row if row is not None else agent.blocked(cfg.plan[key], "not_pulled")
        rows = [fixed[key] for key in _plan_order(cfg)]
        manifest = PullManifest(
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            account_key=ctx.account_key,
            config_version=ctx.config_version,
            created_at=deps.clock.now(),
            rows=tuple(rows),
        )
        await deps.manifests.save(manifest)
        stored = sum(r.status == "stored" for r in rows)
        capped = agent.capped
        return {
            "pending_spend_row": None,
            "counters": {
                "rows_stored": stored,
                **({"budget_exhausted": 1} if capped else {}),
                "rows_blocked": len(rows) - stored,
                "pull_cost_micros": manifest.total_cost_micros(),
            },
            "exits": {"pull": "all_rows_pulled"},
        }

    async def spend_gate(state: AbmState) -> dict[str, Any]:
        """Pause for a spend approval after the credit gate refused a paid call (G7)."""
        decision = interrupt(
            {"awaiting": "cap", "reason": "spend", "row": state.get("pending_spend_row")}
        )
        return {
            "spend_declined": not bool(decision),
            "spend_pauses": state.get("spend_pauses", 0) + 1,
            "pending_spend_row": None,
        }

    def after_pull(state: AbmState) -> str:
        if state.get("pending_spend_row"):
            return "spend_gate"
        return "finish" if state["mode"] == "pull" else "load_manifest"

    # Phase 2 -----------------------------------------------------------------------------------
    async def load_manifest(state: AbmState) -> dict[str, Any]:
        """Phase 2 entry: the build precondition on the stored manifest (G16)."""
        ctx = context(state)
        source = state["run_id"] if state["mode"] == "full" else state.get("parent_run_id")
        manifest = await deps.manifests.for_run(ctx.tenant_id, UUID(source)) if source else None
        try:
            pre = check_build_precondition(
                manifest,
                tenant_id=ctx.tenant_id,
                account_key=ctx.account_key,
                required_rows=cfg.plan,
                shelf_life_days=cfg.shelf_life_days,
                now=deps.clock.now(),
                acknowledged_stale=frozenset(state.get("acknowledged_stale") or ()),
            )
        except NoPulledData as error:
            return _fail(error.code, missing_rows=list(error.missing))
        except StalePullsNeedDecision as error:
            return _fail(error.code, missing_rows=list(error.rows))
        return {
            "manifest_digest": pre.manifest.digest(),
            "counters": {"stale_acknowledged": len(pre.acknowledged_stale)},
        }

    async def _manifest(state: AbmState) -> PullManifest:
        ctx = context(state)
        source = state["run_id"] if state["mode"] == "full" else state["parent_run_id"]
        manifest = await deps.manifests.for_run(ctx.tenant_id, UUID(str(source)))
        if manifest is None or manifest.digest() != state.get("manifest_digest"):
            raise GuardViolation("G16", "manifest_changed")
        return manifest

    async def _views(ctx: RunContext, rows: list[ManifestRow]) -> list[PayloadView]:
        # a row stores one payload per item per phrasing: a feed returns the same records for
        # each of its phrasings, and two searches often return the same page. Each payload is
        # read once (audit 2026-10-21: news records reached the extractor six times)
        refs = dict.fromkeys(ref for r in rows for ref in r.payload_refs)
        return [await deps.payloads.view(ctx.tenant_id, ref) for ref in refs]

    async def _interpret(
        ctx: RunContext,
        views: list[PayloadView],
        consent_ok: bool,
        lock: EntityLock | None = None,
    ) -> Counter[str]:
        drops: Counter[str] = Counter()
        candidates: list[GradingCandidate] = []
        people: list[Any] = []
        lanes = (await deps.settings.get(ctx.tenant_id)).lanes or cfg.lanes
        # a page that is not about the account is never read for facts (staging 2026-10-16: a
        # run on one company summarised another whose page a search had returned)
        kept = [
            v
            for v in views
            # the client's own material (internal history) is about the account by construction;
            # environment rows (competitors, the sector) are about other companies by design
            # feeds pulled by the account's own domain or organization id are about it by
            # construction, though a record's text rarely names it (audit 2026-10-21)
            if v.visibility != "public"
            or v.row_key in OFF_TARGET_EXEMPT_ROWS
            or cfg.plan[v.row_key].need in ACCOUNT_KEYED_NEEDS
            or (
                on_target(ctx.domain, ctx.account_name, v.url, v.text)
                # the entity lock (section 3): a look-alike's host or a page that names only
                # the look-alike is another company's, however often it says the brand
                and (lock is None or accept(lock, v.url, v.text)[0])
            )
        ]
        if len(kept) < len(views):
            drops["off_target"] += len(views) - len(kept)
            _log.info(
                "interpret.off_target",
                extra={"count": len(views) - len(kept), "rows": len(views)},
            )
        views = kept
        # every source group's findings are graded and saved as soon as that group's last call
        # returns, so the client sees facts while the rest of the node is still reading
        # (audit 2026-10-21: every fact used to wait for the slowest call of the whole node)
        try:
            await _extract_steps(ctx, views, consent_ok, drops, candidates, people, lanes)
        except Exception:
            # a model failure part-way (credits, usage limit, outage): grade and keep what the
            # groups still in hand found before the run stops, so a stopped run shows its
            # findings instead of nothing (audit 2026-10-15); a cancelled slice is not this
            if candidates or people:
                # the original failure is what the run reports; a grading error must not hide it
                with contextlib.suppress(Exception):
                    await _grade_and_record(ctx, candidates, people, drops)
            raise
        return drops

    async def _extract_steps(
        ctx: RunContext,
        views: list[PayloadView],
        consent_ok: bool,
        drops: Counter[str],
        candidates: list[GradingCandidate],
        people: list[Any],
        lanes: tuple[str, ...] = (),
    ) -> None:
        grading_lock = asyncio.Lock()
        # the same (row, attribute, value) found by two source groups is one fact: the second
        # is graded only when it brings a full-page read the first lacked (a Confirmed upgrade)
        seen: dict[tuple[str, str, str], bool] = {}
        # the token guard reached during a step: calls already in flight finish and are kept
        # (a cancelled call would be paid again after the user continues), no new one starts,
        # and the node pauses once the step's calls are done
        guard: list[TokenGuardReached] = []
        # one gate for the three steps: their rows are disjoint (a checklist row has exactly one
        # step), so the steps read side by side and a step's slowest call no longer holds the
        # next step back; the calls in flight stay at EXTRACT_CONCURRENCY in all
        gate = asyncio.Semaphore(EXTRACT_CONCURRENCY)

        async def run_step(step: Literal["pass1", "pass2", "people"]) -> None:
            if guard:
                raise guard[0]
            if step == "people" and not consent_ok:
                return
            if step == "people" and not getattr(deps.grading, "records_people", True):
                # nothing can record people yet: the extraction call is not paid for only to be
                # thrown away (OI-ABM-4; audit 2026-10-15)
                drops["people_extraction_skipped"] += 1
                return
            m = meter(step)

            async def one(
                group: ExtractionInput,
                m: BudgetMeter = m,
                gate: asyncio.Semaphore = gate,
                agent: AgentContract = reg[step],
            ) -> ExtractionResult | None:
                async with gate:
                    if guard:
                        return ExtractionResult()
                    try:
                        return await extract(agent, deps.model, m, ctx, group, cfg, lanes)
                    except TokenGuardReached as reached:
                        guard.append(reached)
                        return ExtractionResult()
                    except BudgetExceeded:
                        return None
                    except ContextTooLong:
                        # one group too large for the model's window is dropped and counted;
                        # the other groups still extract (observed 2026-10-12 as a run that died
                        # on "prompt is too long")
                        res = ExtractionResult()
                        res.drops["context_too_long"] += 1
                        return res

            async def need(inputs: list[ExtractionInput], step: str = step) -> None:
                """One route need's batches side by side (kept in order), then its findings
                graded and saved at once. A checklist row has exactly one step and one need
                (``extraction.groups``), so a need holds every candidate of its rows: grading it
                alone sees the same evidence per attribute as grading the whole node."""
                found: list[GradingCandidate] = []
                met: list[Any] = []
                for res in await _all_or_none(one(g) for g in inputs):
                    if res is None:
                        drops[f"{step}_budget_exceeded"] += 1
                        continue
                    for item in res.candidates:
                        c = item.candidate
                        key = (c.row_key, c.attribute, value_key(c.value))
                        if key in seen and (seen[key] or not item.read_this_run):
                            drops["duplicate_across_groups"] += 1
                            continue
                        seen[key] = item.read_this_run
                        found.append(item)
                    met += res.people
                    drops.update(res.drops)
                # held as the node's ungraded findings until saved, so a failure in another
                # need still salvages them (see _interpret)
                candidates.extend(found)
                people.extend(met)
                async with grading_lock:
                    # one grading at a time: the engine pins the run's config and reads its
                    # lead states per batch
                    await _grade_and_record(ctx, found, met, drops)
                saved = {id(x) for x in (*found, *met)}
                candidates[:] = [c for c in candidates if id(c) not in saved]
                people[:] = [p for p in people if id(p) not in saved]

            by_need: dict[str, list[ExtractionInput]] = {}
            for g in groups(cfg, views, step):
                by_need.setdefault(g.group, []).append(g)
            await _all_or_none(need(inputs) for inputs in by_need.values())

        await _all_or_none(run_step(step) for step in STEPS)
        if guard:
            raise guard[0]

    async def _grade_and_record(
        ctx: RunContext,
        candidates: list[GradingCandidate],
        people: list[Any],
        drops: Counter[str],
    ) -> None:
        batch = await deps.grading.grade(ctx, candidates)
        reads = batch.read_in_primary or tuple(
            candidates[i].read_this_run for i in batch.item_index
        )
        for fact, index, read in zip(batch.facts, batch.item_index, reads, strict=True):
            lead_check(candidates[index].candidate.origin, read, fact.grade)
        drops.update(batch.drops)
        if people:
            try:
                await deps.grading.record_people(ctx, people)
            except NotConfigured:
                drops["people_not_recorded"] += len(people)

    def model_guarded(name: str, fn: Any) -> Any:
        """A model node whose terminal model failure ends the run partial (see MODEL_FAILURES)
        and whose token guard pauses it at ``token_gate`` (which re-runs node ``name``)."""

        async def guarded(state: AbmState) -> dict[str, Any]:
            """Run the node; a terminal model failure becomes a partial outcome."""
            try:
                out: dict[str, Any] = await fn(state)
                return out
            except TokenGuardReached as reached:
                _log.info(
                    "token_guard.reached",
                    extra={"count": reached.tokens, "rows": reached.threshold},
                )
                return {"token_pause_node": name}
            except Exception as error:  # noqa: BLE001 - re-raised unless it is a model failure
                code = model_failure_code(error)
                if code is None:
                    raise
                return _fail(code)

        guarded.__name__ = fn.__name__
        return guarded

    async def token_gate(state: AbmState) -> dict[str, Any]:
        """Pause and ask once the run's tokens reached its token guard (owner decision
        2026-10-23). Continuing raises the guard by its multiple of the estimate again and re-runs
        the paused node; stopping ends the run with what it found."""
        decision = interrupt(
            {"awaiting": "cap", "reason": "tokens", "node": state.get("token_pause_node")}
        )
        pauses = state.get("token_pauses", 0) + 1
        if not decision:
            return _fail("token_guard_stopped", token_pause_node=None, token_pauses=pauses)
        await deps.credit.accept_tokens(context(state))
        return {
            "token_pause_node": None,
            "token_resume_node": state.get("token_pause_node"),
            "token_pauses": pauses,
        }

    def after_token_gate(state: AbmState) -> str:
        if state.get("outcome") == "failed":
            return "finish"
        return str(state.get("token_resume_node") or "finish")

    async def interpret(state: AbmState) -> dict[str, Any]:
        """Steps 2 to 4, interpretation: extract, grade and report every row to the tracker."""
        ctx = context(state)
        manifest = await _manifest(state)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        consent_ok = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version) is None
        stored = [r for r in manifest.rows if r.status == "stored"]
        # report every row first: the tracker needs only the pull's call refs, so a run that
        # stops during extraction still shows the steps it pulled (audit 2026-10-15). The
        # posting rows are the job pipeline's: it reports them itself with its own evidence
        # (board searches, feed, reads) in the ``pipelines`` node; a second report from here
        # would be a refused transition (done to partial)
        for row in manifest.rows:
            if cfg.plan[row.row_key].pipeline in ("jobs", "people") and deps.pipelines is not None:
                continue  # the pipelines node reports these rows with its own evidence
            await deps.tracker.report(ctx, row.row_key, row.call_refs, row.blocked_reason)
        views = await _views(ctx, stored)
        drops = await _interpret(ctx, views, consent_ok, _lock(state))
        # the cloud logs carry no message text, so a run that read many pages and kept no fact
        # said nothing about why (staging 2026-10-21, 27 extraction calls and no facts): one line
        # per drop reason with its count, never text
        _log.info("interpret.read", extra={"count": len(views), "rows": len(stored)})
        for reason, n in sorted(drops.items()):
            _log.info("interpret.drop", extra={"error_code": reason[:80], "count": n})
        careers = await _careers(ctx, stored)
        return {
            "counters": {f"drop_{k}": v for k, v in drops.items()},
            "careers_url": careers,
            "exits": {"interpret": "extracted"},
        }

    async def pipelines(state: AbmState) -> dict[str, Any]:
        """The code pipelines after the reading step (skill steps 6 and 7): postings read,
        classified and counted by the job pipeline; technology detections as dated rows."""
        ctx = context(state)
        if deps.pipelines is None:
            return {"exits": {"pipelines": "not_available_yet"}}
        # a build run runs the pipelines too (G14: full equals pull then build); their feed and
        # search calls replay from the ledger or the research cache when the parent made them
        in_scope = _in_scope(state, cfg)
        counters: dict[str, int] = {}
        exits: dict[str, str] = {}
        jobs_rows = {k for k, row in cfg.plan.items() if row.pipeline == "jobs"}
        manifest = await _manifest(state)
        pulled = {r.row_key: r for r in manifest.rows}
        if jobs_rows & in_scope:
            try:
                out = await deps.pipelines.run_jobs(ctx, state.get("careers_url"))
            except TokenGuardReached:
                raise
            except Exception as error:  # noqa: BLE001 - a model failure ends the run partial
                if model_failure_code(error) is not None:
                    raise
                # the pipeline failed for another reason: the run goes on and the posting rows
                # keep what Phase 1 pulled, with the failure as their reason (never silent, G1)
                _log.warning(
                    "pipelines.jobs_failed", extra={"error_code": type(error).__name__[:80]}
                )
                for key in sorted(jobs_rows & in_scope):
                    row = pulled.get(key)
                    await deps.tracker.report(
                        ctx, key, row.call_refs if row else [], "source_failed"
                    )
                counters["pipelines_jobs_failed"] = 1
                exits["jobs"] = "failed"
            else:
                counters.update(out.counters)
                exits.update(out.exits)
        else:
            for key in sorted(jobs_rows):
                row = pulled.get(key)
                if row is not None:
                    await deps.tracker.report(ctx, key, row.call_refs, row.blocked_reason)
            exits["jobs"] = "not_planned"
        tech_rows = {k for k, row in cfg.plan.items() if row.pipeline == "tech"}
        if tech_rows & in_scope:
            try:
                out = await deps.pipelines.write_detections(ctx)
            except TokenGuardReached:
                raise
            except Exception as error:  # noqa: BLE001 - a store or feed error never ends the run
                if model_failure_code(error) is not None:
                    raise
                _log.warning(
                    "pipelines.tech_failed", extra={"error_code": type(error).__name__[:80]}
                )
                for key in sorted(tech_rows & in_scope):
                    await deps.tracker.report(ctx, key, [], "source_failed")
                counters["pipelines_tech_failed"] = 1
                exits["tech"] = "failed"
            else:
                counters.update(out.counters)
                exits.update(out.exits)
            # the organisation's tech stack (the Confirmed stack sheet): its own failure never
            # touches the detection rows or ends the run
            try:
                stack_out = await deps.pipelines.write_stack(ctx)
            except TokenGuardReached:
                raise
            except Exception as error:  # noqa: BLE001 - a store or lookup error never ends the run
                if model_failure_code(error) is not None:
                    raise
                _log.warning(
                    "pipelines.stack_failed", extra={"error_code": type(error).__name__[:80]}
                )
                counters["pipelines_stack_failed"] = 1
                exits["stack"] = "failed"
            else:
                counters.update(stack_out.counters)
                exits.update(stack_out.exits)
        else:
            exits["tech"] = "not_planned"
        # the people list (skill step 8), under the tenant's people data consent (G8): without
        # it the three rows end Blocked consent_required and nothing is pulled
        people_rows = {k for k, row in cfg.plan.items() if row.pipeline == "people"}
        if people_rows & in_scope:
            consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
            problem = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version)
            if problem is not None:
                for key in sorted(people_rows & in_scope):
                    await deps.tracker.report(ctx, key, [], "consent_required")
                exits["people"] = "consent_required"
            else:
                try:
                    out = await deps.pipelines.run_people(ctx)
                except TokenGuardReached:
                    raise
                except Exception as error:  # noqa: BLE001 - a model failure ends the run partial
                    if model_failure_code(error) is not None:
                        raise
                    _log.warning(
                        "pipelines.people_failed",
                        extra={"error_code": type(error).__name__[:80]},
                    )
                    for key in sorted(people_rows & in_scope):
                        await deps.tracker.report(ctx, key, [], "source_failed")
                    counters["pipelines_people_failed"] = 1
                    exits["people"] = "failed"
                else:
                    counters.update(out.counters)
                    exits.update(out.exits)
        else:
            for key in sorted(people_rows):
                row = pulled.get(key)
                if row is not None:
                    await deps.tracker.report(ctx, key, row.call_refs, row.blocked_reason)
            exits["people"] = "not_planned"
        return {"counters": counters, "exits": {**exits, "pipelines": "run"}}

    async def person_searches(state: AbmState) -> dict[str, Any]:
        """Step 9 (checklist): speaking, podcasts and video searched by person name, never by
        company name, for the executives the reading step found. Free searches with their page
        reads; a credit refusal or a failed source ends the step, never the run."""
        ctx = context(state)
        in_scope = _in_scope(state, cfg) & set(PERSON_ROWS)
        if not in_scope:
            return {"exits": {"person_searches": "not_planned"}}
        names = people_named(await deps.grading.facts(ctx))
        queries = person_queries(names, ctx.account_name, sorted(in_scope))
        views: list[PayloadView] = []
        calls: dict[str, list[str]] = {}
        failed = 0

        async def search_one(item: tuple[str, str]) -> Any:
            """One person-name search; a refusal or failed source is a ``None`` result."""
            row_key, query = item
            try:
                return await deps.provider.call(ctx, "web_search", query, row_key)
            except (CapPause, ProviderFailed):
                return None

        # side by side, then stored and read back in the order of the queries
        for (row_key, _query), result in zip(
            queries, await fan_out_all(queries, search_one), strict=True
        ):
            if result is None:
                failed += 1
                continue
            calls.setdefault(row_key, []).extend(result.call_refs)
            for source in result.items:
                ref = await deps.payloads.put(ctx, row_key, source, "public")
                views.append(await deps.payloads.view(ctx.tenant_id, ref))
        for row_key, refs in calls.items():
            await deps.tracker.report(ctx, row_key, refs, None)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        ok = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version) is None
        drops = await _interpret(ctx, views, ok) if views else Counter[str]()
        return {
            "counters": {
                "people_named": len(names),
                "person_searches": len(queries) - failed,
                **({"person_searches_failed": failed} if failed else {}),
                **{f"drop_{k}": v for k, v in drops.items()},
            },
            "exits": {"person_searches": "searched" if queries else "no_names"},
        }

    async def _careers(ctx: RunContext, stored: list[ManifestRow]) -> str | None:
        from agents.schemas import RunRequest, VisitedPage
        from agents.setup.agent import choose_careers_page, validate_setup

        settings = await deps.settings.get(ctx.tenant_id)
        pages: list[Any] = []
        for row in stored:
            if cfg.plan[row.row_key].need in ("scrape", "page_read"):
                for ref in row.payload_refs:
                    view = await deps.payloads.view(ctx.tenant_id, ref)
                    pages.append(
                        VisitedPage(
                            page_id=f"page{len(pages) + 1}",  # payload keys are not ids
                            url=view.url,
                            title=cfg.row_labels[row.row_key][:300],
                        )
                    )
        if not pages:
            return None
        setup_input = validate_setup(
            RunRequest(account_name=ctx.account_name, domain=ctx.domain),
            settings,
            cfg,
            0,
            tuple(pages[:20]),
        )
        try:
            url, _ = await choose_careers_page(
                reg["setup"], deps.model, meter("setup"), ctx, setup_input
            )
        except BudgetExceeded:
            return None
        return url

    async def sense(state: AbmState) -> dict[str, Any]:
        """Step 5a: the mandatory sense check and the routing of its proposals (G6, D-96)."""
        ctx = context(state)
        # the last round records what is left; earlier rounds pull for it (deep runs more rounds)
        depth = (await deps.settings.get(ctx.tenant_id)).depth
        rounds = cfg.follow_up_rounds.get(depth, cfg.follow_up_rounds.get("standard", 1))
        second = state.get("sense_pass", 0) >= rounds
        facts = await deps.grading.facts(ctx)
        inp = SenseCheckInput(
            manifest_digest=state["manifest_digest"],
            account_name=ctx.account_name,
            domain=ctx.domain,
            facts=facts,
        )
        try:
            res = await sense_check(
                reg["sense_check"], deps.model, meter("sense_check"), ctx, inp, cfg
            )
        except BudgetExceeded:
            return _fail("sense_check_budget_exceeded")
        for finding in res.contradictions:
            await deps.grading.mark_conflicting(ctx, finding.fact_ids, "sense_check_contradiction")
        to_run: list[dict[str, str]] = []
        awaiting: list[dict[str, str]] = []
        cap = await deps.credit.cap_state(ctx)
        in_scope = _in_scope(state, cfg)
        for p in res.proposals:
            if p.row_key not in in_scope:
                continue  # a follow-up outside the job's scope is not pulled (or paid for)
            need = cfg.plan[p.row_key].need
            estimate, known = await deps.credit.price(need)
            route, _ = spend_route(cap, need, estimate, known, deps.free_operations, ctx.attended)
            item = {"row_key": p.row_key, "query": p.query, "need": need}
            if second or route == "record":
                await deps.tracker.mark_partial(ctx, p.row_key, "pull_not_approved")
            elif route == "run":
                to_run.append(item)
            else:
                awaiting.append(item)
        out: dict[str, Any] = {
            "sense_pass": state.get("sense_pass", 0) + 1,
            "to_run": to_run,
            "awaiting": awaiting,
            "counters": {
                "sense_findings": len(res.findings),
                **{f"sense_drop_{k}": v for k, v in res.drops.items()},
            },
        }
        if not to_run and not awaiting:
            current = await deps.grading.facts(ctx)
            await deps.records.save_sense_check(
                ctx,
                SenseCheckRecord(
                    ctx.run_id,
                    state["manifest_digest"],
                    facts_digest(current),
                    len(res.findings),
                    len(res.proposals),
                ),
            )
            out["exits"] = {"sense_check": "second_pass_recorded" if second else "checked"}
        return out

    def after_sense(state: AbmState) -> str:
        if state.get("outcome") == "failed":
            return "finish"
        if state.get("awaiting"):
            return "sense_approval"
        return "targeted_pulls" if state.get("to_run") else "distill"

    async def sense_approval(state: AbmState) -> dict[str, Any]:
        """Pause for approval of recommended pulls the cap does not cover (D-96)."""
        ctx = context(state)
        awaiting = state.get("awaiting") or []
        total = 0
        for item in awaiting:
            total += (await deps.credit.price(item["need"]))[0]
        approval_id = await deps.credit.request_approval(ctx, awaiting, total)
        decision = interrupt(
            {"awaiting": "cap", "reason": "sense_pulls", "approval_id": approval_id}
        )
        if decision:
            return {"to_run": [*(state.get("to_run") or []), *awaiting], "awaiting": []}
        for item in awaiting:
            await deps.tracker.mark_partial(ctx, item["row_key"], "pull_not_approved")
        return {"awaiting": []}

    async def targeted_pulls(state: AbmState) -> dict[str, Any]:
        """Run approved or covered pulls, then interpret and grade what they returned."""
        ctx = context(state)
        views: list[PayloadView] = []
        capped = False
        pulls: list[dict[str, str]] = list(state.get("to_run") or [])

        async def pull_one(item: dict[str, str]) -> Any:
            """One follow-up pull; a refusal or failed source is returned, not raised."""
            try:
                return await deps.provider.call(ctx, item["need"], item["query"], item["row_key"])
            except (CapPause, ProviderFailed) as error:
                return error

        for item, result in zip(pulls, await fan_out_all(pulls, pull_one), strict=True):
            if isinstance(result, (CapPause, ProviderFailed)):
                # the true reason (a failed source is not an unapproved pull)
                await deps.tracker.mark_partial(ctx, item["row_key"], result.reason)
                capped |= isinstance(result, CapPause) and result.reason == CREDIT_LIMIT
                continue
            for source in result.items:
                ref = await deps.payloads.put(ctx, item["row_key"], source, "public")
                views.append(await deps.payloads.view(ctx.tenant_id, ref))
            await deps.tracker.report(ctx, item["row_key"], result.call_refs, None)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        ok = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version) is None
        drops = await _interpret(ctx, views, ok) if views else Counter[str]()
        return {
            "to_run": [],
            "counters": {
                "targeted_pulls": len(state.get("to_run") or []),
                **({"budget_exhausted": 1} if capped else {}),
                **{f"drop_{k}": v for k, v in drops.items()},
            },
        }

    async def distill_node(state: AbmState) -> dict[str, Any]:
        """Step 5: distill for the client audience. The Internal Only file is built from the
        graded facts and the tracker (``documents.internal_doc``), so the second, internal
        distill call that nothing read is no longer made (audit 2026-10-15: one planner call
        over every fact, per run, for nothing)."""
        ctx = context(state)
        facts = await deps.grading.facts(ctx)
        counters: dict[str, int] = {}
        statable = frozenset(f.fact_id for f in facts if may_state(f.grade))
        try:
            res = await distill(
                reg["distill"],
                deps.model,
                meter("distill"),
                ctx,
                facts,
                "public",
                statable,
                focus=state.get("goal"),
            )
        except BudgetExceeded:
            return _fail("distill_budget_exceeded")
        await deps.records.save_distill(ctx, "client", res.output)
        counters.update({f"distill_client_drop_{k}": v for k, v in res.drops.items()})
        _log.info(
            "distill.done",
            extra={
                "count": len(res.output.narrative),
                "dropped": sum(res.drops.values()),
                "rows": len(facts),
            },
        )
        return {"counters": counters, "exits": {"distill": "distilled"}}

    # Steps 6 to 8: code only -------------------------------------------------------------------
    async def build(state: AbmState) -> dict[str, Any]:
        """Step 6 (code): build files after the sense-check precondition; scan every file (G9)."""
        ctx = context(state)
        current: tuple[StoredFact, ...] = await deps.grading.facts(ctx)
        if not current:
            _log.info("build.no_findings", extra={"count": 0, "outcome": NO_NEW_FINDINGS})
            return {"outcome": "completed", "error_code": NO_NEW_FINDINGS}
        try:
            require_sense_check(
                await deps.records.sense_check(ctx),
                state["manifest_digest"],
                facts_digest(current),
            )
        except GuardViolation as missing:
            return _fail(missing.code)
        try:
            client = await deps.builder.build(ctx, "client")
            internal = await deps.builder.build(ctx, "internal")
        except BuildIncomplete as incomplete:
            # the builder refused the set (missing narrative, an unconfirmed statement in the
            # Summary): the research stands and the run ends partial with the builder's reason,
            # never FAILED (observed 2026-10-12 as four runs that lost their pulls this way)
            reason = str(
                getattr(incomplete, "codes", "")
                or getattr(incomplete, "reason", "")
                or "build_failed"
            )
            return {"outcome": "partial", "error_code": f"build_failed:{reason}"}
        try:
            # client files only: the Internal Only file is never a client file (two outputs)
            for file_id in client:
                assert_client_clean(await deps.builder.texts(ctx, file_id))
        except GuardViolation as leak:
            return _fail(leak.code)
        return {"client_files": list(client), "internal_files": list(internal)}

    async def gate(state: AbmState) -> dict[str, Any]:
        """Step 7 (code): the quality gate and the coverage verdict (G1)."""
        ctx = context(state)
        failing = await deps.gate.check(ctx)
        rows = await deps.tracker.rows(ctx)
        # G1: every row ends with a status and proof. A row no step reported (nothing pulls it
        # yet, or the research never reached it) ends Not done "not available yet" here, so one
        # unreported row no longer holds every file back (staging 2026-10-16: two rows did)
        unreported = sorted(
            key for key in cfg.plan if rows.get(key) is None or rows[key].status not in ROW_TERMINAL
        )
        for key in unreported:
            await deps.tracker.report(ctx, key, [], "not_available_yet")
        if unreported:
            rows = await deps.tracker.rows(ctx)
        verdict = coverage_verdict(cfg.plan, rows)
        # a run-level failure holds every file back; a file-level one (an item naming a client
        # file: a leak against an upload, a lost row) holds back that file and the rest are
        # delivered. Until 2026-10-22 any defect in any file ended the run with nothing
        # delivered, and each fix moved the stop one check later (staging 2026-10-21).
        from documents.gate import FILE_CHECKS, withheld_files

        run_level = {k: v for k, v in failing.items() if v and k not in FILE_CHECKS}
        file_items = [(k, i) for k, v in failing.items() if k in FILE_CHECKS for i in v]
        held = withheld_files({"check": k, "item": i} for k, i in file_items)
        # a file-level item that names no client file cannot be held back by file: it is
        # run-level (fail closed), never ignored
        if any(i.split(":", 1)[0] not in held for _, i in file_items):
            run_level = {
                **run_level,
                **{k: v for k, v in failing.items() if k in FILE_CHECKS and v},
            }
        # each check with its items (file kind and count, never text): the log said only
        # "client_dashes" on 2026-10-21 and not which of the five files carried them
        names = [f"{k}[{';'.join(failing[k])[:50]}]" for k in sorted(failing) if failing[k]]
        if run_level or not verdict.deliverable:
            if not verdict.deliverable:
                stuck = [*verdict.missing, *verdict.unproven][:6]
                names.append("coverage_verdict:" + ",".join(stuck))
            _log.warning(
                "gate.failed",
                extra={
                    "error_code": ";".join(names)[:200],
                    "count": sum(len(v) for v in failing.values()),
                    "rows": len(verdict.missing) + len(verdict.unproven),
                },
            )
            return {
                "outcome": "partial",
                "error_code": "gate_failed",
                "missing_rows": [*verdict.missing, *verdict.unproven],
            }
        if held:
            _log.warning(
                "gate.withheld",
                extra={"error_code": ";".join(names)[:200], "count": len(held), "rows": 0},
            )
            return {
                "outcome": "partial",
                "error_code": FILES_WITHHELD,
                "withheld_files": sorted(held),
                "counters": {"rows_closed_at_gate": len(unreported)} if unreported else {},
            }
        return {"counters": {"rows_closed_at_gate": len(unreported)}} if unreported else {}

    def after_build(state: AbmState) -> str:
        done = state.get("outcome") in ("failed", "partial")
        return "finish" if done or state.get("error_code") == NO_NEW_FINDINGS else "gate"

    def after_gate(state: AbmState) -> str:
        code = state.get("error_code")
        return "deliver" if code in (None, FILES_WITHHELD) else "finish"

    async def deliver(state: AbmState) -> dict[str, Any]:
        """Step 8 (code): release the client files the gate passed; never outreach. A file the
        gate held back is named to the delivery, which records it; the run stays partial."""
        ctx = context(state)
        withheld = list(state.get("withheld_files") or [])
        await deps.delivery.release(ctx, state.get("client_files") or [], withheld=withheld)
        rows = await deps.tracker.rows(ctx)
        # a stated gap the client chose (internal history not switched on) is not a missing step:
        # the tracker's completion policy allows it the same way
        # (config/tracker_v1.yaml high_reasons_allowed_for_completed)
        complete = not withheld and all(
            r.status in ("done", "none") or r.reason in STATED_GAPS for r in rows.values()
        )
        return {"outcome": "completed" if complete else "partial"}

    async def finish(state: AbmState) -> dict[str, Any]:
        """The single settlement point of every run."""
        if state.get("settled"):
            return {}
        await deps.wallet.settle_once(context(state), False)
        outcome = state.get("outcome", "running")
        if outcome == "running":
            outcome = "completed"
        return {"settled": True, "outcome": outcome}

    def token_paused(route: Any) -> Any:
        """A model node's edge: to ``token_gate`` when the token guard paused it."""
        return lambda state: "token_gate" if state.get("token_pause_node") else route(state)

    def ok_or_finish(target: str) -> Any:
        return token_paused(lambda state: "finish" if state.get("outcome") == "failed" else target)

    g: StateGraph[AbmState] = StateGraph(AbmState)
    for name, fn in (
        ("setup", setup),
        ("entity_lock", entity_lock),
        ("cap_approval", cap_approval),
        ("pull", pull),
        ("spend_gate", spend_gate),
        ("load_manifest", load_manifest),
        ("interpret", model_guarded("interpret", interpret)),
        ("pipelines", model_guarded("pipelines", pipelines)),
        ("person_searches", model_guarded("person_searches", person_searches)),
        ("sense_check", model_guarded("sense_check", sense)),
        ("sense_approval", sense_approval),
        ("targeted_pulls", model_guarded("targeted_pulls", targeted_pulls)),
        ("distill", model_guarded("distill", distill_node)),
        ("token_gate", token_gate),
        ("build", build),
        ("gate", gate),
        ("deliver", deliver),
        ("finish", finish),
    ):
        # The node RetryPolicy is the single retry owner for retryable provider errors (W6): a
        # broker 429/5xx in the pulls, and a model overload, timeout or dropped connection in the
        # model nodes (stored replies replay, so a retry never pays twice for a finished call).
        policy = retry_policy if name in RETRIED_NODES else None
        g.add_node(name, cast(Any, fn), retry_policy=policy)
    g.add_edge(START, "setup")
    g.add_conditional_edges(
        "setup", lambda s: "finish" if s.get("outcome") == "failed" else "entity_lock"
    )
    g.add_conditional_edges(
        "entity_lock",
        lambda s: (
            "finish"
            if s.get("outcome") == "failed"
            else ("load_manifest" if s["mode"] == "build" else "cap_approval")
        ),
    )
    g.add_edge("cap_approval", "pull")
    g.add_conditional_edges("pull", after_pull)
    g.add_edge("spend_gate", "pull")
    g.add_conditional_edges("load_manifest", ok_or_finish("interpret"))
    g.add_conditional_edges("interpret", ok_or_finish("pipelines"))
    g.add_conditional_edges("pipelines", ok_or_finish("person_searches"))
    g.add_conditional_edges("person_searches", ok_or_finish("sense_check"))
    g.add_conditional_edges("sense_check", token_paused(after_sense))
    g.add_conditional_edges(
        "token_gate",
        after_token_gate,
        [
            "interpret",
            "pipelines",
            "person_searches",
            "sense_check",
            "targeted_pulls",
            "distill",
            "finish",
        ],
    )
    g.add_edge("sense_approval", "targeted_pulls")
    g.add_conditional_edges("targeted_pulls", ok_or_finish("sense_check"))
    g.add_conditional_edges("distill", ok_or_finish("build"))
    g.add_conditional_edges("build", after_build)
    g.add_conditional_edges("gate", after_gate)
    g.add_edge("deliver", "finish")
    g.add_edge("finish", END)
    return g
