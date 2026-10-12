"""Credit cap adapters for the workflow (thin; ``spend`` owns the logic).

* ``propose_and_gate`` (setup): builds the run's cap proposal from the workflow plan and the
  provider cost table after the free balance preflight, stores it, and lets the tenant policy
  auto-approve it only within the run's created budget (proposal D-28). A legacy run (no cap
  requirement) passes unchanged.
* ``after_approval`` (first stage after the approval gate): the careers lookup deferred by setup
  (conflict C12) runs once, now that a cap exists.
* the end-of-run report is stored by ``runs.lifecycle.finish`` in the finishing transaction
  (every terminal path, K12), not here.

GUARANTEE(test_setup_without_policy_waits_and_makes_no_paid_call): fail closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.state import RunState
from spend.config import load_spend_config
from spend.errors import ApprovalInvalid, ProposalStale
from spend.models import PlanItem, Principal


@dataclass(frozen=True)
class GateOutcome:
    """Whether paid calls may start now, and the cap state for the run record."""

    approved: bool
    state: str
    version: int | None = None


async def propose_and_gate(deps: RunDeps) -> GateOutcome:
    """Propose the cap; auto-approve within the tenant policy; otherwise wait for a person."""
    from account_intelligence.connectors.settings import load_providers_config
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.workflow.handlers import people_operations
    from spend.costs import cost_table
    from spend.plan import spec_plan, stage_order

    costs = cost_table(load_providers_config())
    plan = spec_plan(
        deps.spec, costs, load_grading_config().category_by_operation, people_operations()
    )
    return await propose_plan_and_gate(deps, plan, stage_order(deps.spec))


async def propose_plan_and_gate(
    deps: RunDeps, plan: Sequence[PlanItem], order: Sequence[str]
) -> GateOutcome:
    """Propose the cap for a given plan (Workflow v1's stage plan or the ABM pull plan) and let
    the tenant policy auto-approve it within the created budget; the one non-API approver (K3)."""
    from account_intelligence.connectors.settings import load_providers_config
    from spend import service
    from spend.costs import cost_table

    async with deps.txn(deps.tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT r.cap_required, r.cap_version, t.policy_json FROM account_runs r"
                    " JOIN tenants t ON t.id = r.tenant_id WHERE r.id = :r"
                ),
                {"r": deps.run_id},
            )
        ).one()
    if not row.cap_required:
        return GateOutcome(True, "legacy")
    if row.cap_version is not None:
        return GateOutcome(True, "approved", row.cap_version)
    cfg = load_spend_config()
    costs = cost_table(load_providers_config())
    proposed = await service.propose(
        deps.txn,
        deps.tenant_id,
        deps.run_id,
        plan,
        costs,
        getattr(deps, "balances", None),
        cfg,
        deps.clock.now(),
        order,
        getattr(deps, "notifier", None),
    )
    policy: dict[str, Any] = row.policy_json or {}
    if policy.get("auto_approve_within_reserve") or getattr(deps, "dev_auto_approve", False):
        principal = Principal(
            kind="tenant_policy",
            subject="auto_approve_within_reserve",
            tenant_id=str(deps.tenant_id),
        )
        try:
            await service.approve(
                deps.txn,
                deps.tenant_id,
                deps.run_id,
                proposed.version,
                proposed.proposal.content_hash,
                principal,
                cfg,
                deps.clock.now(),
            )
            return GateOutcome(True, "approved", proposed.version)
        except (ApprovalInvalid, ProposalStale):
            return GateOutcome(False, "awaiting_approval", proposed.version)
    return GateOutcome(False, "awaiting_approval", proposed.version)


async def after_approval(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """The careers lookup setup deferred until the cap was approved (runs once)."""
    from account_intelligence.workflow.stages.core import verify_careers

    if (state.get("references") or {}).get("careers") != "deferred_until_approval":
        return {}
    # its own key: the reducer never lets a later write change setup's "careers" value
    return {"references": {"careers_after_approval": await verify_careers(deps)}}
