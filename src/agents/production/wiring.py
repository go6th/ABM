"""Build the agent layer for one run from the workflow's ``RunDeps`` and plug it into the runner.

* ``abm_deps`` binds every port to its production adapter for one run.
* ``RunDepsModel`` is the ``AgentModel``: ``RunDeps.model`` routes by task kind (router or legacy
  tier, recorded) and ``llm.schema.validate`` checks the output (one implementation each).
* ``AbmGraphFactory`` is what ``workflow.runner.execute_slice`` runs instead of Workflow v1 for an
  ABM run: the compiled graph, its initial state from the run row, and the terminal mapping.
* ``uses_abm`` decides per run: a ``pull`` or ``build`` run always (those modes exist only here);
  a run started by the research job engine (``run_input.job_id``) or the public-sources run
  linked to a file analysis (``run_input.linked_file_run``) always, since a job's scope
  (jobs, people, full, topic) is defined on the agent graph's rows; any other ``full`` run only
  when the tenant's ``abm_agents`` flag is on (deny by default, C34).

GUARANTEE(test_runner_executes_an_abm_run_end_to_end_on_postgres)
GUARANTEE(test_full_runs_stay_on_workflow_v1_unless_the_flag_is_on)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text

from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.platform.ports import NotConfigured
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.stages.grading_stages import LlmJudge
from account_intelligence.workflow.transitions import RunStatus
from agents.adapters import PgConsentStore, PgManifestStore
from agents.config import SkillConfig, skill_config
from agents.contracts import load_registry
from agents.graph import NO_NEW_FINDINGS, AbmDeps, AbmState, build_abm_graph
from agents.ports import RunContext
from agents.production.credit import CreditAdapter
from agents.production.delivery import ExportBuilderPort, GateAdapter, NoteDelivery, RunnerSettles
from agents.production.grading import EngineGrading, InheritedLedger
from agents.production.payloads import StorePayloads
from agents.production.provider import ConnectorProvider
from agents.production.state import PgRunRecords, SettingsAdapter
from agents.production.tracker import TrackerAdapter
from llm.errors import SchemaInvalid
from llm.schema import REASK_NOTE, validate

M = TypeVar("M", bound=BaseModel)
FLAG = "abm_agents"
PRE_SPEND_FAILURES = frozenset({"no_pulled_data", "stale_pulls", "invalid_setup"})


@dataclass(frozen=True)
class RunDepsModel:
    """``AgentModel`` over ``RunDeps.model`` (routed or legacy, recorded) and schema validation."""

    deps: RunDeps

    async def call(
        self,
        task_kind: str,
        prompt_text: str,
        prompt_hash: str,
        payload: Mapping[str, Any],
        output: type[M],
        ctx: RunContext,
    ) -> tuple[M, int]:
        """One call with the output's JSON Schema in the instructions (the prompt says "matching
        the schema"; without the schema itself the model invents field names) and as structured
        output, so a Claude reply cannot fail it (token review item 12, 2026-10-20). A reply that
        still fails it is asked once more; a second failure is ``SchemaInvalid`` (never kept). A
        reply cut off at its token limit is asked again with twice the limit (up to
        ``MAX_REASK_TOKENS``): the same limit would cut it off again."""
        import json

        from agents.skill_context import with_skill_context
        from llm.tasks import MAX_REASK_TOKENS, require

        json_schema = output.model_json_schema()
        schema = json.dumps(json_schema, separators=(",", ":"))
        # the owner's skill travels with every call (operator request 2026-10-16); it sits in
        # the cached system blocks, so it is paid for once per cache window
        answer_rule = (
            "Answer with one JSON object, no prose and no code fence, that validates against"
            f" this JSON Schema:\n{schema}"
        )
        prompt = with_skill_context(prompt_text, task_kind)
        # the schema joins the least shared part, keeping the shared parts cacheable across kinds
        extended = getattr(prompt, "extended", None)
        system = extended(answer_rule) if extended else f"{prompt}\n\n{answer_rule}"
        user = json.dumps(dict(payload))
        tokens = 0
        override: str | None = None
        limit = require(task_kind).max_tokens
        cut_off = False
        # LOOP(agent schema re-ask): cap=2 attempts, then 2 on the Claude default when the
        # chosen model is Kimi K3; exit=valid reply, SchemaInvalid
        for attempt in range(4):
            if attempt == 2:
                override = await self._fallback(task_kind)
                if override is None:
                    break
            if cut_off:
                limit = min(limit * 2, MAX_REASK_TOKENS)
            result = await self.deps.model(
                task_kind,
                system,
                user + (REASK_NOTE if attempt % 2 and not cut_off else ""),
                model=override,
                schema=json_schema,
                max_tokens=limit,
            )
            tokens += int(result.usage.input_tokens + result.usage.output_tokens)
            validated = validate(result.text, output)
            if validated is not None:
                return validated, tokens
            cut_off = str(result.extra.get("stop_reason") or "") == "max_tokens"
        raise SchemaInvalid(f"{task_kind}: output failed its schema")

    async def _fallback(self, task_kind: str) -> str | None:
        """A step whose chosen model is not Claude and could not meet the strict schema falls
        back to the Claude default of its role; the run records the step and why (owner
        decision 2026-10-14). None when there is nothing to fall back to."""
        from llm.choice import catalogue, claude_default
        from llm.tasks import LEGACY_TIER

        tier = LEGACY_TIER[task_kind]
        chosen = self.deps.chosen_model(tier)
        model = catalogue().get(chosen) if chosen else None
        if model is None or model.provider == "anthropic":
            return None
        role = "research" if tier in catalogue().research_tiers else "analysis"
        target = claude_default(role)
        recorder = self.deps.models
        if recorder is None:  # no run to record on
            return target
        recorder.fell_back(task_kind, chosen, target, "schema_invalid")
        from account_intelligence.runs import lifecycle

        async with self.deps.txn(self.deps.tenant_id) as s:
            await lifecycle.record_models(s, self.deps.run_id, recorder.as_json())
        return target


class _NoConnectors:
    """Provider port for a process with no connector runtime: every pull is not available yet."""

    async def call(self, ctx: RunContext, need: str, query: str, row_key: str) -> Any:
        from agents.ports import ProviderFailed

        raise ProviderFailed("not_supported_yet")


def abm_deps(deps: RunDeps, lanes: tuple[str, ...] = (), cfg: SkillConfig | None = None) -> AbmDeps:
    """Every agent port bound to its production adapter for this run."""
    cfg = cfg or skill_config()
    grading_cfg = load_grading_config()
    provider: Any = _NoConnectors()
    ledger: InheritedLedger | None = None
    if deps.connectors is not None:
        provider = ConnectorProvider(
            deps.connectors,
            deps.account_id,
            lanes,
            deps.extra_allowed_hosts,
            reuse_cached=deps.reuse_cached,
            resume_from=deps.resume_from,
        )
        ledger = InheritedLedger(
            LedgerAdapter(
                deps.txn, deps.connectors.payloads, deps.connectors.connectors, grading_cfg
            ),
            deps.txn,
        )
    if ledger is None:
        raise NotConfigured("the agent layer needs the connector runtime (ledger and sources)")
    from spend.config import load_spend_config

    return AbmDeps(
        cfg=cfg,
        registry=load_registry(),
        clock=deps.clock,
        model=RunDepsModel(deps),
        provider=provider,
        payloads=StorePayloads(deps.payloads, deps.account_id),
        manifests=PgManifestStore(),
        consent=PgConsentStore(),
        settings=SettingsAdapter(deps.txn, deps.run_id, cfg),
        grading=EngineGrading(deps.txn, ledger, grading_cfg, deps.clock, LlmJudge(deps)),
        tracker=TrackerAdapter(deps.txn, deps.spec.coverage_owners),
        credit=CreditAdapter(deps.txn, deps.clock, cfg, deps),
        records=PgRunRecords(deps.txn),
        builder=ExportBuilderPort(deps),
        gate=GateAdapter(deps),
        delivery=NoteDelivery(deps),
        wallet=RunnerSettles(),
        internal=None,  # D-98: no connector search binding yet; internal history stays off
        free_operations=frozenset(load_spend_config().free_operations),
    )


async def uses_abm(deps: RunDeps) -> bool:
    """See the module docstring."""
    from admin import flags

    async with deps.txn(deps.tenant_id) as s:
        mode = (
            await s.execute(
                text("SELECT run_mode FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one_or_none()
        if mode in ("pull", "build"):
            return True
        job = (
            await s.execute(
                text(
                    "SELECT 1 FROM account_runs WHERE id = :r AND input_mode = 'company'"
                    " AND (run_input ? 'job_id' OR run_input ? 'linked_file_run')"
                ),
                {"r": deps.run_id},
            )
        ).first()
        if job is not None:
            return True  # a credit-confirmed research job runs the skill-shaped agent graph
        rule = await flags.current(s, FLAG)
        plan = (
            await s.execute(text("SELECT plan FROM tenants WHERE id = :t"), {"t": deps.tenant_id})
        ).scalar_one_or_none()
    return flags.evaluate(rule, FLAG, deps.tenant_id, str(plan or ""))


@dataclass(frozen=True)
class AbmGraphFactory:
    """The runner's alternative graph for an ABM run (``workflow.runner.GraphFactory``)."""

    cfg: SkillConfig | None = None

    async def build(self, deps: RunDeps, saver: Any) -> Any:
        """The compiled ABM graph on the run's checkpointer."""
        cfg = self.cfg or skill_config()
        async with deps.txn(deps.tenant_id) as s:
            run_input: dict[str, Any] = (
                await s.execute(
                    text("SELECT run_input FROM account_runs WHERE id = :r"), {"r": deps.run_id}
                )
            ).scalar_one_or_none() or {}
            # the tenant's own lanes (settings), not the skill's list: people search follows them
            from tenancy import settings as tenant_settings

            configured = (await tenant_settings.current(s)).settings.platform_lanes
        off = set(dict(run_input).get("lanes_off") or ())
        lanes = tuple(lane for lane in configured if lane not in off)
        policy = None
        if deps.connectors is not None:
            from account_intelligence.connectors.service import retry_policy_for

            policy = retry_policy_for(deps.connectors.config)
        return build_abm_graph(abm_deps(deps, lanes, cfg), policy).compile(checkpointer=saver)

    async def initial(self, deps: RunDeps) -> dict[str, Any]:
        """The ABM state from the run row (tenant and run from the trusted run path)."""
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT r.run_mode, r.parent_run_id, r.skill_config_version, r.attended,"
                        " r.run_input, r.budget_usd, a.domain, a.name FROM account_runs r"
                        " JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                    ),
                    {"r": deps.run_id},
                )
            ).first()
        if row is None:
            raise LookupError("run not found for this tenant")
        run_input = dict(row.run_input or {})
        cfg = self.cfg or skill_config()
        state: AbmState = {
            "tenant_id": str(deps.tenant_id),
            "run_id": str(deps.run_id),
            "mode": str(row.run_mode or "full"),
            "parent_run_id": str(row.parent_run_id) if row.parent_run_id else None,
            "account_key": row.domain,
            "account_name": row.name or row.domain,
            "domain": row.domain,
            "attended": bool(row.attended),
            "config_version": row.skill_config_version or cfg.config_version,
            "acknowledged_stale": list(run_input.get("acknowledged_stale_rows") or ()),
            "cap_request_micros": 0,
            "scope_rows": list(run_input.get("scope_rows") or ()) or None,
            "topic": run_input.get("topic") or None,
            "goal": run_input.get("goal") or None,
            "goal_rows": list(run_input.get("goal_rows") or ()),
            "counters": {},
            "exits": {},
        }
        return dict(state)

    def target(self, values: Mapping[str, Any]) -> tuple[RunStatus, str | None, bool]:
        """``(terminal status, reason, refund_all)`` of a finished ABM graph."""
        outcome = values.get("outcome")
        code = values.get("error_code")
        capped = bool((values.get("counters") or {}).get("budget_exhausted"))
        if outcome == "failed" and code in PRE_SPEND_FAILURES:
            return RunStatus.FAILED, str(code), True
        if capped:
            # the run used the credits confirmed for it: it stopped paying and kept what it
            # found (its files were built from that); the job is marked credit_cap_reached
            return RunStatus.BUDGET_EXCEEDED, "budget_cap", False
        if values.get("mode") == "pull" and outcome == "completed":
            return RunStatus.PARTIAL, "pull_only", False  # Phase 1 evaluates no rows (G15)
        if outcome == "completed":
            # nothing new found is a finished research, not a stop (no Resume offered)
            return RunStatus.COMPLETED, (str(code) if code == NO_NEW_FINDINGS else None), False
        # delivered, with steps that could not be finished (blocked, waiting on the owner, not
        # approved): finished with gaps, never "stopped" (audit 2026-10-15)
        return RunStatus.PARTIAL, str(code or "steps_waiting"), False


def run_context(values: Mapping[str, Any]) -> RunContext:
    """The run context of a stored ABM state (for adapters used outside the graph)."""
    return RunContext(
        UUID(str(values["tenant_id"])),
        UUID(str(values["run_id"])),
        str(values["account_key"]),
        str(values["account_name"]),
        str(values["domain"]),
        bool(values.get("attended")),
        str(values["config_version"]),
    )
