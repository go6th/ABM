"""Cap approval rules (K3, K1). Pure: the repository applies them inside one transaction.

A decision on a cap version is valid only when:

* the principal is a human with an approver role, or the tenant policy within its limit (an
  initial cap at or below the run's created budget, with no provider excluded for want of a
  balance); an ``agent`` principal is always refused (no agent tool can approve or extend);
* the principal's tenant is the run's tenant;
* the proposal hash given equals the stored version's hash (a changed proposal fails);
* the version is still undecided (single use: the database's unique decision index also refuses
  a second decision) and not expired;
* the balance snapshots the proposal was built on are not older than the configured age;
* the cap is not below the spend already made, and an extension is strictly higher than the
  current cap.

GUARANTEE(test_approval_binding_rejects_every_mutated_field): K3.
GUARANTEE(test_cap_never_lowered_below_spend): K1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from spend.config import SpendConfig
from spend.errors import ApprovalInvalid, ProposalStale
from spend.models import Principal

DECISIONS = frozenset({"approved", "rejected", "expired", "extension_declined"})


@dataclass(frozen=True)
class VersionRecord:
    """What the repository read about one cap version."""

    run_id: str
    run_tenant_id: str
    version: int
    cap_micros: int
    proposal_hash: str
    created_at: datetime
    events: tuple[str, ...]
    snapshot_times: tuple[datetime, ...]
    is_extension: bool
    has_exclusions: bool = False  # a provider was excluded for want of a usable balance


def state_of(events: Sequence[str]) -> str:
    """``proposed`` until a decision event exists; then that decision."""
    for event in events:
        if event in DECISIONS:
            return event
    return "proposed"


def check_principal(
    principal: Principal, record: VersionRecord, cfg: SpendConfig, run_budget_micros: int | None
) -> None:
    """The principal may decide this version (``ApprovalInvalid`` otherwise)."""
    if principal.tenant_id != record.run_tenant_id:
        raise ApprovalInvalid("principal belongs to another tenant")
    if principal.kind == "agent":
        raise ApprovalInvalid("an agent cannot approve or extend a cap")
    if principal.kind == "human":
        if principal.role not in cfg.approver_roles:
            raise ApprovalInvalid("role may not approve a cap")
        return
    # tenant policy: only an initial cap within the run's created budget (proposal D-28)
    if cfg.auto_approve != "within_run_budget" or record.is_extension:
        raise ApprovalInvalid("tenant policy may not approve this cap")
    if run_budget_micros is None or record.cap_micros > run_budget_micros:
        raise ApprovalInvalid("cap is above the tenant policy limit")
    if record.has_exclusions:
        raise ApprovalInvalid("a proposal with excluded providers needs a person to approve it")


def check_binding(
    record: VersionRecord,
    principal: Principal,
    proposal_hash: str,
    now: datetime,
    cfg: SpendConfig,
    *,
    run_budget_micros: int | None,
    spent_micros: int,
    current_cap_micros: int | None,
) -> None:
    """Every rule of the module docstring; raises ``ApprovalInvalid`` or ``ProposalStale``."""
    check_principal(principal, record, cfg, run_budget_micros)
    if proposal_hash != record.proposal_hash:
        raise ProposalStale("the proposal changed since it was shown")
    if state_of(record.events) != "proposed":
        raise ApprovalInvalid("this cap version was already decided")
    ttl = cfg.extension_ttl_minutes if record.is_extension else cfg.approval_ttl_minutes
    if now - record.created_at > timedelta(minutes=ttl):
        raise ProposalStale("the proposal expired")
    oldest_ok = now - timedelta(minutes=cfg.max_snapshot_age_minutes)
    if any(taken < oldest_ok for taken in record.snapshot_times):
        raise ProposalStale("the balance snapshot is stale: propose again")
    if record.cap_micros < spent_micros:
        raise ApprovalInvalid("a cap cannot be lower than the spend already made")
    if record.is_extension and (
        current_cap_micros is None or record.cap_micros <= current_cap_micros
    ):
        raise ApprovalInvalid("an extension must be strictly higher than the current cap")
