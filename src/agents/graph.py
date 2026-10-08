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

GUARANTEE(test_full_equals_pull_then_build)
GUARANTEE(test_pull_mode_produces_a_manifest_and_nothing_else)
GUARANTEE(test_build_never_runs_phase_one)
GUARANTEE(test_sense_check_always_precedes_build)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from account_intelligence.workflow.state import merge_maps, sum_counters
from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.distill.agent import distill
from agents.errors import BudgetExceeded, GuardViolation, NoPulledData, StalePullsNeedDecision
from agents.extraction import ExtractionResult, extract, groups
from agents.guards import (
    consent_decision,
    coverage_verdict,
    facts_digest,
    internal_history_allowed,
    lead_check,
    require_sense_check,
    spend_route,
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
from agents.pull.agent import PullAgent, SpendPause
from agents.schemas import PayloadView, SenseCheckInput, StoredFact
from agents.sense_check.agent import sense_check

MAX_SPEND_PAUSES = 3
STEPS: tuple[Literal["pass1", "pass2", "people"], ...] = ("pass1", "pass2", "people")
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


def _plan_order(cfg: SkillConfig) -> list[str]:
    return sorted(cfg.plan, key=lambda k: (STEP_ORDER[cfg.plan[k].step], k))


def build_abm_graph(deps: AbmDeps) -> StateGraph[AbmState]:
    """The uncompiled graph; compile with the run checkpointer (see the module docstring)."""
    cfg, reg = deps.cfg, deps.registry

    def meter(name: str) -> BudgetMeter:
        return BudgetMeter(reg[name], started_at=deps.clock.now())

    # Step 1 ------------------------------------------------------------------------------------
    async def setup(state: AbmState) -> dict[str, Any]:
        """Step 1: per-client settings must be valid before anything runs."""
        ctx = context(state)
        settings = await deps.settings.get(ctx.tenant_id)
        if not settings.pursuing_client:
            return _fail("invalid_setup")
        if set(settings.lanes) - set(cfg.lanes):
            return _fail("invalid_setup")
        return {"scope": state["mode"], "outcome": "running", "exits": {"setup": "validated"}}

    async def cap_approval(state: AbmState) -> dict[str, Any]:
        """G7 / S16: the cap request is confirmed before any paid call of Phase 1."""
        if state.get("outcome") == "failed" or state["mode"] == "build":
            return {}
        cap = await deps.credit.cap_state(context(state))
        if not cap.cap_required or cap.cap_version is not None:
            return {}
        if not state.get("attended"):
            return {"exits": {"cap_approval": "unattended_no_cap"}}
        decision = interrupt(
            {"awaiting": "cap", "cap_request_micros": state.get("cap_request_micros", 0)}
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
        agent.record_refusals = bool(
            state.get("cap_declined")
            or state.get("spend_declined")
            or state.get("spend_pauses", 0) >= MAX_SPEND_PAUSES
        )
        m = meter("pull")
        rows: list[ManifestRow] = []
        for key in _plan_order(cfg):
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
        return {
            "pending_spend_row": None,
            "counters": {
                "rows_stored": stored,
                "rows_blocked": len(rows) - stored,
                "pull_cost_micros": manifest.total_cost_micros(),
            },
            "exits": {"pull": "all_rows_pulled"},
        }

    async def spend_gate(state: AbmState) -> dict[str, Any]:
        """Pause for a spend approval after the credit gate refused a paid call (G7)."""
        decision = interrupt({"awaiting": "spend", "row": state.get("pending_spend_row")})
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
        people = []
        for step in STEPS:
            if step == "people" and not consent_ok:
                continue
            m = meter(step)
            for group in groups(cfg, views, step):
                try:
                    res: ExtractionResult = await extract(reg[step], deps.model, m, ctx, group, cfg)
                except BudgetExceeded:
                    drops[f"{step}_budget_exceeded"] += 1
                    break
                candidates += res.candidates
                people += res.people
                drops.update(res.drops)
        batch = await deps.grading.grade(ctx, candidates)
        for fact, index in zip(batch.facts, batch.item_index, strict=True):
            item = candidates[index]
            lead_check(item.candidate.origin, item.read_this_run, fact.grade)
        drops.update(batch.drops)
        if people:
            await deps.grading.record_people(ctx, people)
        return drops

    async def interpret(state: AbmState) -> dict[str, Any]:
        """Steps 2 to 4, interpretation: extract, grade and report every row to the tracker."""
        ctx = context(state)
        manifest = await _manifest(state)
        consent = await deps.consent.current(ctx.tenant_id, cfg.consent_scope)
        consent_ok = consent_decision(consent, cfg.consent_scope, cfg.consent_text_version) is None
        stored = [r for r in manifest.rows if r.status == "stored"]
        drops = await _interpret(ctx, await _views(ctx, stored), consent_ok)
        for row in manifest.rows:
            await deps.tracker.report(ctx, row.row_key, row.call_refs, row.blocked_reason)
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
        pages = []
        for row in stored:
            if cfg.plan[row.row_key].need in ("scrape", "page_read"):
                for ref in row.payload_refs:
                    view = await deps.payloads.view(ctx.tenant_id, ref)
                    pages.append(
                        VisitedPage(
                            page_id=ref.replace("/", ":"),
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
        second = state.get("sense_pass", 0) >= 1
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
        for p in res.proposals:
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
        decision = interrupt({"awaiting": "sense_pulls", "approval_id": approval_id})
        if decision:
            return {"to_run": [*(state.get("to_run") or []), *awaiting], "awaiting": []}
        for item in awaiting:
            await deps.tracker.mark_partial(ctx, item["row_key"], "pull_not_approved")
        return {"awaiting": []}

    async def targeted_pulls(state: AbmState) -> dict[str, Any]:
        """Run approved or covered pulls, then interpret and grade what they returned."""
        ctx = context(state)
        views: list[PayloadView] = []
        for item in state.get("to_run") or []:
            try:
                result = await deps.provider.call(ctx, item["need"], item["query"], item["row_key"])
            except (CapPause, ProviderFailed):
                await deps.tracker.mark_partial(ctx, item["row_key"], "pull_not_approved")
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
                **{f"drop_{k}": v for k, v in drops.items()},
            },
        }

    async def distill_node(state: AbmState) -> dict[str, Any]:
        """Step 5: distill for the client and the Internal Only audiences."""
        ctx = context(state)
        facts = await deps.grading.facts(ctx)
        counters: dict[str, int] = {}
        for role, audience in (("public", "client"), ("internal", "internal")):
            try:
                res = await distill(reg["distill"], deps.model, meter("distill"), ctx, facts, role)  # type: ignore[arg-type]
            except BudgetExceeded:
                return _fail("distill_budget_exceeded")
            await deps.records.save_distill(ctx, audience, res.output)
            counters.update({f"distill_{audience}_drop_{k}": v for k, v in res.drops.items()})
        return {"counters": counters, "exits": {"distill": "distilled"}}

    # Steps 6 to 8: code only -------------------------------------------------------------------
    async def build(state: AbmState) -> dict[str, Any]:
        """Step 6 (code): build files after the sense-check precondition; scan every file (G9)."""
        ctx = context(state)
        current: tuple[StoredFact, ...] = await deps.grading.facts(ctx)
        try:
            require_sense_check(
                await deps.records.sense_check(ctx),
                state["manifest_digest"],
                facts_digest(current),
            )
        except GuardViolation as missing:
            return _fail(missing.code)
        client = await deps.builder.build(ctx, "client")
        internal = await deps.builder.build(ctx, "internal")
        try:
            for file_id in (*client, *internal):
                assert_client_clean(await deps.builder.texts(ctx, file_id))
        except GuardViolation as leak:
            return _fail(leak.code)
        return {"client_files": list(client), "internal_files": list(internal)}

    async def gate(state: AbmState) -> dict[str, Any]:
        """Step 7 (code): the quality gate and the coverage verdict (G1)."""
        ctx = context(state)
        failing = await deps.gate.check(ctx)
        verdict = coverage_verdict(cfg.plan, await deps.tracker.rows(ctx))
        if failing or not verdict.deliverable:
            return {
                "outcome": "partial",
                "error_code": "gate_failed",
                "missing_rows": [*verdict.missing, *verdict.unproven],
            }
        return {}

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
        ("interpret", interpret),
        ("sense_check", sense),
        ("sense_approval", sense_approval),
        ("targeted_pulls", targeted_pulls),
        ("distill", distill_node),
        ("build", build),
        ("gate", gate),
        ("deliver", deliver),
        ("finish", finish),
    ):
        g.add_node(name, cast(Any, fn))
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
    g.add_edge("interpret", "sense_check")
    g.add_conditional_edges("sense_check", after_sense)
    g.add_edge("sense_approval", "targeted_pulls")
    g.add_edge("targeted_pulls", "sense_check")
    g.add_conditional_edges("distill", ok_or_finish("build"))
    g.add_conditional_edges("build", ok_or_finish("gate"))
    g.add_conditional_edges("gate", after_gate)
    g.add_edge("deliver", "finish")
    g.add_edge("finish", END)
    return g
