"""Credit cap services: propose, approve, reject, expire, extend, register a pre-pulled file and
close a run. Each runs in one tenant transaction; every state change is an insert or one
conditional UPDATE (duplicate delivery is harmless). No model is used anywhere in this package.

GUARANTEE(test_double_approval_and_approve_versus_expire_race_decide_once): one decision.
GUARANTEE(test_close_run_twice_stores_one_report): one report per terminal state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend import prepulled, repo
from spend.approval import check_binding, check_principal, state_of
from spend.balances import preflight
from spend.config import SpendConfig
from spend.errors import ApprovalInvalid, CapNotApproved, ExtensionDenied, ProposalStale
from spend.models import (
    BalanceSnapshot,
    CapProposal,
    OpCost,
    PlanItem,
    Principal,
    Rollup,
    SpendReport,
    TenantSpendView,
)
from spend.money import round_up_to_step, usd_to_micros
from spend.ports import BalancePort, Notifier
from spend.proposal import build_proposal
from spend.reconcile import reconcile_run
from spend.report import build_report, tenant_view
from spend.rollup import aggregate, ledger_events

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
CAP_GRANULE_MICROS = 100  # account_runs.budget_usd has 4 decimals


@dataclass(frozen=True)
class Proposed:
    """A stored proposal and its version."""

    version: int
    proposal: CapProposal
    reused: bool


async def propose(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    plan: Sequence[PlanItem],
    costs: Mapping[tuple[str, str], OpCost],
    balances: BalancePort | None,
    cfg: SpendConfig,
    now: datetime,
    stage_order: Sequence[str],
    notifier: Notifier | None = None,
) -> Proposed:
    """Run the free balance preflight, build the proposal in code and store it as a new version
    (an identical undecided proposal is reused; an approved cap is never re-proposed)."""
    snaps = await preflight(balances, {i.provider_code for i in plan}, now)
    proposal = build_proposal(str(run_id), plan, costs, snaps, cfg, now, stage_order)
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id)
        if run is None:
            raise CapNotApproved("run not found for this tenant")
        latest = await repo.latest_version(s, run_id)
        if latest:
            record = await repo.version_record(s, run_id, latest, run.tenant_id)
            stored = await repo.proposal_of(s, run_id, latest)
            if record is not None and state_of(record.events) == "approved":
                return Proposed(latest, CapProposal.model_validate(stored), True)
            if (
                record is not None
                and state_of(record.events) == "proposed"
                and (record.proposal_hash == proposal.content_hash)
            ):
                return Proposed(latest, proposal, True)
            if record is not None and state_of(record.events) == "proposed":
                await repo.add_event(
                    s, run_id, latest, "expired", None, "superseded_by_new_proposal"
                )
        version = latest + 1
        await repo.insert_version(
            s,
            run_id,
            version,
            proposal.cap_micros,
            proposal.model_dump(mode="json"),
            proposal.content_hash,
            cfg.config_hash,
            "proposed",
        )
        await repo.save_snapshots(s, run_id, "start", list(snaps.values()))
    if notifier is not None:
        await notifier.prompt(str(run_id), "cap_approval", version)
    return Proposed(version, proposal, False)


async def current_proposal(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> dict[str, Any] | None:
    """The latest cap version as an approver sees it: the ``version`` and ``proposal_hash`` an
    approval binds to (K3), the cap, the decision state and calls per category. No provider code,
    operation or per-line amount leaves this function (D-30). ``None`` when the run has no
    proposal or belongs to another tenant (RLS returns no row)."""
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id, lock=False)
        latest = await repo.latest_version(s, run_id) if run is not None else 0
        if run is None or not latest:
            return None
        record = await repo.version_record(s, run_id, latest, run.tenant_id)
        stored = await repo.proposal_of(s, run_id, latest)
    if record is None or stored is None:
        return None
    calls: dict[str, int] = {}
    for line in stored.get("lines", []):
        category = str(line.get("category", "other"))
        calls[category] = calls.get(category, 0) + int(line.get("calls", 0))
    return {
        "run_id": str(run_id),
        "version": latest,
        "proposal_hash": record.proposal_hash,
        "state": state_of(record.events),
        "cap_micros": record.cap_micros,
        "calls_by_category": dict(sorted(calls.items())),
        "has_exclusions": record.has_exclusions,
        "has_unknown_costs": bool(stored.get("unknown_operations")),
        "is_extension": record.is_extension,
        "proposed_at": record.created_at.isoformat(),
    }


def _sample_ops(proposal_json: dict[str, Any], cfg: SpendConfig) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in proposal_json.get("lines", []):
        if line.get("status") == "sample":
            out[f"{line['provider_code']}:{line['operation']}"] = min(
                int(line["calls"]), cfg.sample_size
            )
    return out


async def approve(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    version: int,
    proposal_hash: str,
    principal: Principal,
    cfg: SpendConfig,
    now: datetime,
) -> int:
    """Bind and record an approval, supersede the earlier approved version and write the run's
    enforcement columns, all in one transaction. Returns the cap in micro-dollars."""
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id)
        if run is None:
            raise ApprovalInvalid("run not found for this tenant")
        record = await repo.version_record(s, run_id, version, run.tenant_id)
        if record is None:
            raise ApprovalInvalid("no such cap version")
        # the budget the run was created with (before any cap is applied): the tenant policy's
        # auto-approval limit (proposal D-28)
        created_budget = run.budget_micros if run.cap_version is None else None
        check_binding(
            record,
            principal,
            proposal_hash,
            now,
            cfg,
            run_budget_micros=created_budget,
            spent_micros=run.spent_micros,
            current_cap_micros=run.budget_micros if run.cap_version is not None else None,
        )
        await repo.add_event(
            s, run_id, version, "approved", f"{principal.kind}:{principal.subject}"
        )
        if run.cap_version is not None:
            await repo.add_event(
                s, run_id, run.cap_version, "superseded", None, f"by version {version}"
            )
        stored = await repo.proposal_of(s, run_id, version) or {}
        cap = round_up_to_step(record.cap_micros, CAP_GRANULE_MICROS)
        await repo.apply_cap(s, run_id, version, cap, _sample_ops(stored, cfg))
    return cap


async def reject(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    version: int,
    principal: Principal,
    cfg: SpendConfig,
    reason: str,
) -> None:
    """Record a human rejection (the run then has no cap: no paid call)."""
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id)
        record = await repo.version_record(s, run_id, version, run.tenant_id) if run else None
        if run is None or record is None:
            raise ApprovalInvalid("no such cap version")
        if principal.kind != "human":
            raise ApprovalInvalid("only a person may reject a cap")
        check_principal(principal, record, cfg, None)
        kind = "extension_declined" if record.is_extension else "rejected"
        await repo.add_event(s, run_id, version, kind, f"human:{principal.subject}", reason[:300])


async def expire_pending(
    txn: TxnFactory, tenant_id: UUID, run_id: UUID, cfg: SpendConfig, now: datetime
) -> list[int]:
    """Expire undecided versions past their time (single decision: a racing approval loses or
    wins, never both). Returns the expired versions."""
    expired: list[int] = []
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id)
        if run is None:
            return expired
        for version in range(1, await repo.latest_version(s, run_id) + 1):
            record = await repo.version_record(s, run_id, version, run.tenant_id)
            if record is None or state_of(record.events) != "proposed":
                continue
            ttl = cfg.extension_ttl_minutes if record.is_extension else cfg.approval_ttl_minutes
            if now - record.created_at > timedelta(minutes=ttl):
                try:
                    await repo.add_event(s, run_id, version, "expired", None, "approval_timeout")
                    expired.append(version)
                except ApprovalInvalid:
                    continue
    return expired


async def request_extension(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    new_cap_micros: int,
    reason: str,
    cfg: SpendConfig,
    notifier: Notifier | None = None,
) -> int:
    """An extension request (D-28): attended runs only, strictly higher than the current cap,
    at most ``max_extensions`` per run, each needing a fresh human approval. Returns the version."""
    async with txn(tenant_id) as s:
        run = await repo.run_money(s, run_id)
        if run is None or run.cap_version is None or run.budget_micros is None:
            raise ExtensionDenied("no approved cap to extend")
        if not run.attended:
            raise ExtensionDenied("an unattended run ends BUDGET_EXCEEDED; no extension")
        count: Any = (
            await s.execute(
                text(
                    "SELECT count(*) FROM run_cap_events WHERE run_id = :r"
                    " AND event = 'extension_requested'"
                ),
                {"r": run_id},
            )
        ).scalar_one()
        if int(count) >= cfg.max_extensions:
            raise ExtensionDenied("extension limit reached")
        if new_cap_micros <= run.budget_micros or new_cap_micros < run.spent_micros:
            raise ExtensionDenied("an extension must be strictly higher than the current cap")
        version = await repo.latest_version(s, run_id) + 1
        body = {
            "extension": True,
            "base_version": run.cap_version,
            "cap_micros": new_cap_micros,
            "reason": reason[:300],
            "snapshot_taken_at": [],
        }
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await repo.insert_version(
            s,
            run_id,
            version,
            new_cap_micros,
            body,
            digest,
            cfg.config_hash,
            "extension_requested",
        )
    if notifier is not None:
        await notifier.prompt(str(run_id), "cap_extension", version)
    return version


async def register_prepulled(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    provider_code: str,
    operation: str,
    body: bytes,
    record_count: int,
    supplied_by: str,
    object_ref: str,
) -> str:
    """Register an operator-supplied file (D-31, ``spend.prepulled``) in its own transaction."""
    async with txn(tenant_id) as s:
        return await prepulled.register(
            s, run_id, provider_code, operation, body, record_count, supplied_by, object_ref
        )


async def close_run(
    txn: TxnFactory,
    tenant_id: UUID,
    run_id: UUID,
    cfg: SpendConfig,
    now: datetime,
    lease_ttl_s: int,
    balances_end: Sequence[BalanceSnapshot] = (),
    llm_micros: int | None = None,
) -> tuple[SpendReport, TenantSpendView, int]:
    """``close_in_session`` in its own tenant transaction (operator tools and tests)."""
    async with txn(tenant_id) as s:
        return await close_in_session(s, run_id, cfg, now, lease_ttl_s, balances_end, llm_micros)


async def close_in_session(
    s: AsyncSession,
    run_id: UUID,
    cfg: SpendConfig,
    now: datetime,
    lease_ttl_s: int,
    balances_end: Sequence[BalanceSnapshot] = (),
    llm_micros: int | None = None,
) -> tuple[SpendReport, TenantSpendView, int]:
    """Reconcile and store the end-of-run report once (same content, same stored version), in
    the caller's transaction: ``lifecycle.finish`` calls it right after the one settlement, so the
    terminal state, the settlement and the report commit together or not at all (K12). Undecided
    cap versions of the ended run are expired (one decision each, K3)."""
    # lock the run first: racing finalisations serialise and store one report (K12)
    run = await repo.run_money(s, run_id)
    if run is None:
        raise CapNotApproved("run not found for this tenant")
    for version in range(1, await repo.latest_version(s, run_id) + 1):
        record = await repo.version_record(s, run_id, version, run.tenant_id)
        if record is not None and state_of(record.events) == "proposed":
            await repo.add_event(s, run_id, version, "expired", None, "run_ended")
    recon = await reconcile_run(s, run_id, now, lease_ttl_s)
    rows = await ledger_events(s, run_id)
    versions = (
        await s.execute(
            text(
                "SELECT v.version, v.cap_usd, e.principal, e.at FROM run_cap_versions v"
                " LEFT JOIN run_cap_events e ON e.run_id = v.run_id AND e.version = v.version"
                " AND e.event = 'approved' WHERE v.run_id = :r ORDER BY v.version"
            ),
            {"r": run_id},
        )
    ).all()
    advisory: dict[str, int] = {}
    if run.cap_version is not None:
        stored = await repo.proposal_of(s, run_id, run.cap_version) or {}
        for line in stored.get("lines", []):
            code = str(line["provider_code"])
            advisory[code] = advisory.get(code, 0) + int(line["expected_micros"])
    blocked = (
        await s.execute(
            text(
                "SELECT reason_code, row_key FROM coverage_rows WHERE run_id = :r AND"
                " reason_code IN ('cap_reached', 'credits_exhausted', 'no_cap', 'budget_cap')"
                " ORDER BY reason_code, row_key"
            ),
            {"r": run_id},
        )
    ).all()
    by_reason: dict[str, tuple[str, ...]] = {}
    for b in blocked:
        by_reason[b.reason_code] = (*by_reason.get(b.reason_code, ()), b.row_key)
    extensions: Any = (
        await s.execute(
            text(
                "SELECT count(*) FROM run_cap_events WHERE run_id = :r AND event ="
                " 'extension_requested'"
            ),
            {"r": run_id},
        )
    ).scalar_one()
    start = await repo.load_snapshots(s, run_id, "start")
    if balances_end:
        await repo.save_snapshots(s, run_id, "end", balances_end)
    rollup = Rollup(
        run_id=str(run_id),
        account_domain=run.account_domain,
        status=run.status,
        attended=run.attended,
        cap_micros=run.budget_micros if run.cap_version is not None else None,
        cap_versions=tuple(
            (
                v.version,
                usd_to_micros(v.cap_usd),
                v.principal,
                v.at.isoformat() if v.at else None,
            )
            for v in versions
        ),
        spent_micros=run.spent_micros,
        ledger_micros=recon.ledger_micros,
        overshoot_micros=run.overshoot_micros,
        by_provider=recon.by_provider,
        by_category=aggregate(rows, lambda r: cfg.category(r.operation)),
        by_operation=aggregate(rows, lambda r: f"{r.operation}:{r.route or 'direct'}"),
        advisory=advisory,
        blocked_rows=by_reason,
        extensions=int(extensions),
        llm_micros=llm_micros,
        balances_start=start,
        balances_end=tuple(balances_end),
        estimated_avoided_micros=None,
    )
    report = build_report(rollup, recon.problems, cfg.variance_threshold_pct)
    view = tenant_view(rollup)
    version = await repo.store_report(
        s,
        run_id,
        report.status,
        report.content_hash,
        report.body,
        view.model_dump(mode="json"),
    )
    return report, view, version


__all__ = [
    "Proposed",
    "ProposalStale",
    "approve",
    "close_in_session",
    "close_run",
    "expire_pending",
    "propose",
    "register_prepulled",
    "reject",
    "request_extension",
]
