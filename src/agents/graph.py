"""The eight-step run as one LangGraph graph with three entry slices (D-102, G14 to G17).

::

    setup -> cap_approval -> pull <-> spend_gate          (Phase 1 slice: modes full, pull)
          -> [pull: finish]
          -> load_manifest -> interpret -> sense_check     (Phase 2 slice: modes full, build)
             -> [sense_approval] -> [targeted_pulls -> sense_check (second, record only)]
             -> distill -> build -> gate -> deliver -> finish

* ``full`` runs the Phase 1 slice and then the Phase 2 slice on its own manifest; ``build`` starts
  the Phase 2 slice on its parent's manifest; ``pull`` stops after Phase 1. The Phase 2 slice
  reads the manifest from the store in every mode, so ``full`` is exactly ``pull`` then ``build``.
* Phase 1 stores payloads and a manifest and nothing else: no grading, tracker verdict, files or
  delivery (G5, G15).
* The build node requires a stored sense check over the same manifest and facts (G6).
* Spending beyond the approved cap pauses through an interrupt in attended runs and is recorded in
  unattended ones (G7, D-96); the only approval points are ``cap_approval``, ``spend_gate`` and
  ``sense_approval``.
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
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured
from account_intelligence.workflow.ports import BuildIncomplete
from account_intelligence.workflow.state import merge_maps, sum_counters
from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.distill.agent import distill
from agents.errors import BudgetExceeded, GuardViolation, NoPulledData, StalePullsNeedDecision
from agents.extraction import ExtractionResult, extract, groups
from agents.guards import (
    OFF_TARGET_EXEMPT_ROWS,
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
MAX_SPEND_PAUSES = 3
STEPS: tuple[Literal["pass1", "pass2", "people"], ...] = ("pass1", "pass2", "people")
# extraction calls in flight at once per step (each call is independent; see _interpret)
EXTRACT_CONCURRENCY = 8
# a run that graded no fact has nothing to build: it ends finished with this reason instead of
# the builder's missing_content (audit 2026-10-20: five McKesson refreshes ended "stopped before
# finishing" and offered a Resume that re-paid the same extraction for the same empty result)
NO_NEW_FINDINGS = "no_new_findings"
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
    error_code: str | None
    missing_rows: list[str]
    careers_url: str | None
    settled: bool
    counters: Annotated[dict[str, int], sum_counters]
    exits: Annotated[dict[str, str], merge_maps]


def context(state: AbmState) -> RunContext:
    """The run context from the trusted run row fields the runner put in the state."""
    return RunContext(
        tenant_id=UUID(state["tenant_id"]),
        run_id=UUID(state["run_id"]),
        account_key=state["account_key"],
        account_name=state["account_name"],
        domain=state["domain"],
        attended=bool(state.get("attended")),
        config_version=state["config_version"],
    )


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


def _in_scope(state: AbmState, cfg: SkillConfig) -> set[str]:
    """The rows this run pulls: its job's scope, or the whole plan."""
    rows = state.get("scope_rows")
    return set(cfg.plan) if not rows else set(rows) & set(cfg.plan)


def _plan_order(cfg: SkillConfig) -> list[str]:
    return sorted(cfg.plan, key=lambda k: (STEP_ORDER[cfg.plan[k].step], k))


RETRIED_NODES = frozenset(
    {"setup", "pull", "interpret", "sense_check", "targeted_pulls", "distill"}
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
        in_scope = _in_scope(state, cfg)
        agent.record_refusals = bool(
            state.get("cap_declined")
            or state.get("spend_declined")
            or state.get("spend_pauses", 0) >= MAX_SPEND_PAUSES
        )
        m = meter("pull")
        rows: list[ManifestRow] = []
        for key in _plan_order(cfg):
            if key not in in_scope:
                # outside this job's scope: reported "not planned for this run", never silent
                rows.append(agent.blocked(cfg.plan[key], "not_planned"))
                continue
            try:
                rows.append(
                    await agent.pull_row(
                        ctx, cfg.plan[key], m, consent_problem=problem, internal_history=internal
                    )
                )
            except SpendPause as pause:
                return {"pending_spend_row": pause.row_key}
            except BudgetExceeded:
                rows.append(agent.blocked(cfg.plan[key], "agent_budget_exceeded"))
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
        return [
            await deps.payloads.view(ctx.tenant_id, ref) for r in rows for ref in r.payload_refs
        ]

    async def _interpret(
        ctx: RunContext, views: list[PayloadView], consent_ok: bool
    ) -> Counter[str]:
        drops: Counter[str] = Counter()
        candidates: list[GradingCandidate] = []
        people: list[Any] = []
        # a page that is not about the account is never read for facts (staging 2026-10-16: a
        # run on one company summarised another whose page a search had returned)
        kept = [
            v
            for v in views
            # the client's own material (internal history) is about the account by construction;
            # environment rows (competitors, the sector) are about other companies by design
            if v.visibility != "public"
            or v.row_key in OFF_TARGET_EXEMPT_ROWS
            or on_target(ctx.domain, ctx.account_name, v.url, v.text)
        ]
        if len(kept) < len(views):
            drops["off_target"] += len(views) - len(kept)
            _log.info(
                "interpret.off_target",
                extra={"count": len(views) - len(kept), "rows": len(views)},
            )
        views = kept
        try:
            await _extract_steps(ctx, views, consent_ok, drops, candidates, people)
        except Exception:
            # a model failure part-way (credits, usage limit, outage): grade and keep what the
            # earlier steps found before the run stops, so a stopped run shows its findings
            # instead of nothing (audit 2026-10-15); a cancelled slice is not this
            if candidates:
                # the original failure is what the run reports; a grading error must not hide it
                with contextlib.suppress(Exception):
                    await _grade_and_record(ctx, candidates, people, drops)
            raise
        await _grade_and_record(ctx, candidates, people, drops)
        return drops

    async def _extract_steps(
        ctx: RunContext,
        views: list[PayloadView],
        consent_ok: bool,
        drops: Counter[str],
        candidates: list[GradingCandidate],
        people: list[Any],
    ) -> None:
        for step in STEPS:
            if step == "people" and not consent_ok:
                continue
            if step == "people" and not getattr(deps.grading, "records_people", True):
                # nothing can record people yet: the extraction call is not paid for only to be
                # thrown away (OI-ABM-4; audit 2026-10-15)
                drops["people_extraction_skipped"] += 1
                continue
            m = meter(step)
            gate = asyncio.Semaphore(EXTRACT_CONCURRENCY)

            async def one(
                group: ExtractionInput,
                m: BudgetMeter = m,
                gate: asyncio.Semaphore = gate,
                agent: AgentContract = reg[step],
            ) -> ExtractionResult | None:
                async with gate:
                    try:
                        return await extract(agent, deps.model, m, ctx, group, cfg)
                    except BudgetExceeded:
                        return None
                    except ContextTooLong:
                        # one group too large for the model's window is dropped and counted;
                        # the other groups still extract (observed 2026-10-12 as a run that died
                        # on "prompt is too long")
                        res = ExtractionResult()
                        res.drops["context_too_long"] += 1
                        return res

            # batches are independent calls: run them side by side, keep their order
            for res in await asyncio.gather(*(one(g) for g in groups(cfg, views, step))):
                if res is None:
                    drops[f"{step}_budget_exceeded"] += 1
                    continue
                candidates += res.candidates
                people += res.people
                drops.update(res.drops)

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

    def model_guarded(fn: Any) -> Any:
        """A model node whose terminal model failure ends the run partial (see MODEL_FAILURES)."""

        async def guarded(state: AbmState) -> dict[str, Any]:
            """Run the node; a terminal model failure becomes a partial outcome."""
            try:
                out: dict[str, Any] = await fn(state)
                return out
            except Exception as error:  # noqa: BLE001 - re-raised unless it is a model failure
                code = model_failure_code(error)
                if code is None:
                    raise
                return _fail(code)

        guarded.__name__ = fn.__name__
        return guarded

    async def interpret(state: AbmState) -> dict[str, Any]:
        """Steps 2 to 4, interpretation: extract, grade and report every row to the tracker."""
        ctx = context(state)
        manifest = await _manifest(state)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        consent_ok = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version) is None
        stored = [r for r in manifest.rows if r.status == "stored"]
        # report every row first: the tracker needs only the pull's call refs, so a run that
        # stops during extraction still shows the steps it pulled (audit 2026-10-15)
        for row in manifest.rows:
            await deps.tracker.report(ctx, row.row_key, row.call_refs, row.blocked_reason)
        drops = await _interpret(ctx, await _views(ctx, stored), consent_ok)
        careers = await _careers(ctx, stored)
        return {
            "counters": {f"drop_{k}": v for k, v in drops.items()},
            "careers_url": careers,
            "exits": {"interpret": "extracted"},
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
        for item in state.get("to_run") or []:
            try:
                result = await deps.provider.call(ctx, item["need"], item["query"], item["row_key"])
            except (CapPause, ProviderFailed) as error:
                # the true reason (a failed source is not an unapproved pull)
                await deps.tracker.mark_partial(ctx, item["row_key"], error.reason)
                capped |= isinstance(error, CapPause) and error.reason == CREDIT_LIMIT
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
                reg["distill"], deps.model, meter("distill"), ctx, facts, "public", statable
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
        if failing or not verdict.deliverable:
            # which checks held the files back (the account card reads the stored verdict; the
            # log said only "gate_failed" before 2026-10-16); row keys are config names
            names = sorted(failing)
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
        return {"counters": {"rows_closed_at_gate": len(unreported)}} if unreported else {}

    def after_build(state: AbmState) -> str:
        done = state.get("outcome") in ("failed", "partial")
        return "finish" if done or state.get("error_code") == NO_NEW_FINDINGS else "gate"

    def after_gate(state: AbmState) -> str:
        return "finish" if state.get("error_code") else "deliver"

    async def deliver(state: AbmState) -> dict[str, Any]:
        """Step 8 (code): release the client files; never outreach."""
        ctx = context(state)
        await deps.delivery.release(ctx, state.get("client_files") or [])
        rows = await deps.tracker.rows(ctx)
        complete = all(r.status in ("done", "none") for r in rows.values())
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

    def ok_or_finish(target: str) -> Any:
        return lambda state: "finish" if state.get("outcome") == "failed" else target

    g: StateGraph[AbmState] = StateGraph(AbmState)
    for name, fn in (
        ("setup", setup),
        ("cap_approval", cap_approval),
        ("pull", pull),
        ("spend_gate", spend_gate),
        ("load_manifest", load_manifest),
        ("interpret", model_guarded(interpret)),
        ("sense_check", model_guarded(sense)),
        ("sense_approval", sense_approval),
        ("targeted_pulls", model_guarded(targeted_pulls)),
        ("distill", model_guarded(distill_node)),
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
        "setup",
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
    g.add_conditional_edges("interpret", ok_or_finish("sense_check"))
    g.add_conditional_edges("sense_check", after_sense)
    g.add_edge("sense_approval", "targeted_pulls")
    g.add_conditional_edges("targeted_pulls", ok_or_finish("sense_check"))
    g.add_conditional_edges("distill", ok_or_finish("build"))
    g.add_conditional_edges("build", after_build)
    g.add_conditional_edges("gate", after_gate)
    g.add_edge("deliver", "finish")
    g.add_edge("finish", END)
    return g
