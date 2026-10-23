"""``CreditPort`` on the credit manager (cap state, the cost table and pause requests).

* ``cap_state`` reads the run's enforcement columns in one statement, as the connector ledger
  does before every paid call (no money moves here).
* ``price`` is the cost table's expected figure for the need's first route (a page read or scrape
  adds the account search that finds the page); an operation with no figure is ``known=False``.
* ``request_approval`` opens the credit manager's pause request for the proposed pulls
  (``spend.pause.compute_request``/``open_pause``): the approver sees the figures with their bases
  and the rows at risk; approving raises the cap version and the run resumes.

GUARANTEE(test_credit_adapter_reads_the_cap_and_opens_a_pause_request)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.platform.ports import Clock
from agents.config import SkillConfig
from agents.ports import RunContext
from agents.production.provider import load_routes
from spend import pause
from spend.config import load_spend_config, load_v2_config
from spend.costs import cost_table
from spend.gate import RunCapState
from spend.limits import read_inputs
from spend.models import PlanItem
from spend.money import usd_to_micros


def pull_plan_items(cfg: SkillConfig, costs: Mapping[tuple[str, str], Any]) -> list[PlanItem]:
    """The ABM pull plan as credit-cap plan items: every planned row's phrasings over its route
    (a page read or scrape adds the account search), only operations the cost table prices."""
    from account_intelligence.tracker.config import load_checklist

    providers = load_providers_config()
    routes = load_routes()
    checklist = load_checklist()
    items: list[PlanItem] = []
    for key, row in sorted(cfg.plan.items()):
        spec = routes.needs.get(row.need)
        if spec is None:
            continue
        ops = [(str(spec["route"]), str(routes.feed_by_row.get(key, spec["operation"])))]
        if spec.get("url_from") == "account_search":
            ops.insert(0, ("open_web_search", "web_search"))
        found = checklist.row(key)
        priority = found.priority if found is not None else "Optional"
        for route_name, op in ops:
            first = providers.route(route_name).first
            code = str(getattr(first.provider, "value", first.provider or ""))
            if (code, op) not in costs:
                continue
            items.append(
                PlanItem(
                    row_key=key,
                    stage=row.step,
                    priority=priority if priority in ("High", "Medium", "Optional") else "Optional",
                    provider_code=code,
                    operation=op,
                    calls=max(len(cfg.phrasings), 1),
                    mandatory=priority == "High",
                )
            )
    return items


@dataclass(frozen=True)
class CreditAdapter:
    """See the module docstring."""

    txn: TxnFactory
    clock: Clock
    cfg: SkillConfig
    deps: Any = None  # the run's RunDeps (the setup adapter proposes and gates the cap)

    async def propose_cap(self, ctx: RunContext) -> bool:
        """The cap proposal of the pull plan, auto-approved only by the tenant policy within the
        created budget, through Workflow v1's setup adapter (the one non-API approver, K3: the
        agent layer never reaches the approval service itself)."""
        if self.deps is None:
            return False
        from account_intelligence.workflow.stages.spend import propose_plan_and_gate

        costs = cost_table(load_providers_config())
        outcome = await propose_plan_and_gate(
            self.deps, pull_plan_items(self.cfg, costs), ("pass1", "pass2", "people")
        )
        return outcome.approved

    async def cap_state(self, ctx: RunContext) -> RunCapState:
        """The run's cap enforcement columns (refused as ``no_cap`` when the run is unknown)."""
        async with self.txn(ctx.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT cap_required, cap_version, budget_usd, spent_usd,"
                        " cap_overshoot_usd FROM account_runs WHERE id = :r"
                    ),
                    {"r": ctx.run_id},
                )
            ).first()
        if row is None:
            return RunCapState(True, None, None, 0, 0, {})
        return RunCapState(
            cap_required=bool(row.cap_required),
            cap_version=row.cap_version,
            budget_micros=usd_to_micros(row.budget_usd) if row.budget_usd is not None else None,
            spent_micros=usd_to_micros(row.spent_usd),
            overshoot_micros=usd_to_micros(row.cap_overshoot_usd),
            sample_calls_left={},
        )

    async def price(self, need: str) -> tuple[int, bool]:
        """``(expected micro-dollars, known)`` for one pull of the need."""
        spec = load_routes().needs.get(need)
        if spec is None:
            return 0, False
        providers = load_providers_config()
        table = cost_table(providers)
        ops = [(str(spec["route"]), str(spec["operation"]))]
        if spec.get("url_from") == "account_search":
            ops.insert(0, ("open_web_search", "web_search"))
        total, known = 0, True
        for route_name, op in ops:
            first = providers.route(route_name).first
            if first.provider is None:
                continue
            code = str(getattr(first.provider, "value", first.provider))
            cost = table.get((code, op))
            if cost is None or not cost.known:
                known = False
                continue
            total += cost.expected_micros
        free = load_spend_config().free_operations
        if all(f"{c}:{o}" in free for c, o in ops):
            return 0, True
        return total, known

    async def accept_tokens(self, ctx: RunContext) -> None:
        """Record the run's tokens now as accepted by the user (``spend_guard.accept_tokens``)."""
        from account_intelligence.connectors import spend_guard

        async with self.txn(ctx.tenant_id) as s:
            await spend_guard.accept_tokens(s, ctx.run_id)

    async def request_approval(
        self, ctx: RunContext, proposals: Sequence[Mapping[str, Any]], total_micros: int
    ) -> str:
        """Open (idempotently) the pause request for the proposed pulls; its id."""
        v2, cfg = load_v2_config(), load_spend_config()
        async with self.txn(ctx.tenant_id) as s:
            inputs = await read_inputs(s, ctx.run_id)
            if inputs is None or inputs.cap_version is None or inputs.cap_micros is None:
                return "no_cap"
            blocked = []
            for p in proposals:
                estimate, known = await self.price(str(p["need"]))
                blocked.append(
                    pause.BlockedUnit(
                        f"sense:{p['row_key']}", estimate if known else None, (str(p["row_key"]),)
                    )
                )
            request = pause.compute_request(
                inputs.cap_version,
                inputs.cap_micros,
                inputs.committed_micros,
                inputs.held_micros,
                blocked,
                cfg.rounding_step_micros,
                cfg.sample_hold_micros,
                v2.tenant_ceiling_micros,
            )
            request_id, _ = await pause.open_pause(s, ctx.run_id, request, self.clock.now(), v2)
        return str(request_id)
