"""Spend persistence on one tenant transaction (``tenant_txn``). Insert-only tables; the only
UPDATE is the conditional write of the run's enforcement columns when a cap version is approved
(``apply_cap``), which never lowers the cap below what is spent and never goes back a version.

Every state change is an insert or one conditional ``UPDATE ... WHERE``, so a duplicate delivery
is harmless; one decision per version is enforced by the database (partial unique index).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spend.approval import VersionRecord
from spend.errors import ApprovalInvalid
from spend.models import BalanceSnapshot
from spend.money import micros_to_usd, usd_to_micros


@dataclass(frozen=True)
class RunMoney:
    """The run's money and cap columns (read FOR UPDATE)."""

    run_id: UUID
    tenant_id: str
    status: str
    cap_required: bool
    cap_version: int | None
    budget_micros: int | None
    spent_micros: int
    overshoot_micros: int
    unreconciled_micros: int
    attended: bool
    account_domain: str


async def run_money(s: AsyncSession, run_id: UUID, *, lock: bool = True) -> RunMoney | None:
    """The run's cap columns, locked for the rest of the transaction when ``lock``."""
    row = (
        await s.execute(
            text(
                "SELECT r.id, r.tenant_id, r.status, r.cap_required, r.cap_version, r.budget_usd,"
                " r.spent_usd, r.cap_overshoot_usd, r.unreconciled_usd, r.attended, a.domain"
                " FROM account_runs r JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                + (" FOR UPDATE OF r" if lock else "")
            ),
            {"r": run_id},
        )
    ).first()
    if row is None:
        return None
    return RunMoney(
        run_id=row.id,
        tenant_id=str(row.tenant_id),
        status=row.status,
        cap_required=row.cap_required,
        cap_version=row.cap_version,
        budget_micros=usd_to_micros(row.budget_usd) if row.budget_usd is not None else None,
        spent_micros=usd_to_micros(row.spent_usd),
        overshoot_micros=usd_to_micros(row.cap_overshoot_usd),
        unreconciled_micros=usd_to_micros(row.unreconciled_usd),
        attended=row.attended,
        account_domain=row.domain,
    )


async def latest_version(s: AsyncSession, run_id: UUID) -> int:
    """The highest cap version of the run (0 when none)."""
    value: Any = (
        await s.execute(
            text("SELECT COALESCE(max(version), 0) FROM run_cap_versions WHERE run_id = :r"),
            {"r": run_id},
        )
    ).scalar_one()
    return int(value)


async def insert_version(
    s: AsyncSession,
    run_id: UUID,
    version: int,
    cap_micros: int,
    proposal_json: dict[str, Any],
    proposal_hash: str,
    config_hash: str,
    event: str,
    principal: str | None = None,
    reason: str | None = None,
) -> None:
    """Insert an immutable cap version and its first event (``proposed`` or
    ``extension_requested``)."""
    await s.execute(
        text(
            "INSERT INTO run_cap_versions (run_id, version, cap_usd, proposal_json, proposal_hash,"
            " config_hash) VALUES (:r, :v, :cap, CAST(:p AS jsonb), :h, :c)"
        ),
        {
            "r": run_id,
            "v": version,
            "cap": micros_to_usd(cap_micros),
            "p": json.dumps(proposal_json, sort_keys=True),
            "h": proposal_hash,
            "c": config_hash,
        },
    )
    await add_event(s, run_id, version, event, principal, reason)


async def add_event(
    s: AsyncSession,
    run_id: UUID,
    version: int,
    event: str,
    principal: str | None = None,
    reason: str | None = None,
) -> None:
    """Append one cap event; a second decision on a version is refused (``ApprovalInvalid``)."""
    try:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO run_cap_events (run_id, version, event, principal, reason)"
                    " VALUES (:r, :v, :e, :p, :why)"
                ),
                {"r": run_id, "v": version, "e": event, "p": principal, "why": reason},
            )
    except IntegrityError as exc:
        raise ApprovalInvalid(f"cap version {version} was already decided") from exc


async def version_record(
    s: AsyncSession, run_id: UUID, version: int, tenant_id: str
) -> VersionRecord | None:
    """One version with its events and the snapshot times its proposal was built on."""
    row = (
        await s.execute(
            text(
                "SELECT cap_usd, proposal_hash, proposal_json, created_at FROM run_cap_versions"
                " WHERE run_id = :r AND version = :v"
            ),
            {"r": run_id, "v": version},
        )
    ).first()
    if row is None:
        return None
    events: Any = (
        await s.execute(
            text("SELECT event FROM run_cap_events WHERE run_id = :r AND version = :v ORDER BY id"),
            {"r": run_id, "v": version},
        )
    ).scalars()
    proposal = row.proposal_json or {}
    times = tuple(_taken_at(item) for item in proposal.get("snapshot_taken_at", []))
    return VersionRecord(
        run_id=str(run_id),
        run_tenant_id=tenant_id,
        version=version,
        cap_micros=usd_to_micros(row.cap_usd),
        proposal_hash=row.proposal_hash,
        created_at=row.created_at,
        events=tuple(events),
        snapshot_times=times,
        is_extension=bool(proposal.get("extension")),
        has_exclusions=bool(proposal.get("excluded_providers")),
    )


def _taken_at(item: str) -> datetime:
    """``code:status:<iso time>`` -> the time (the iso time itself contains colons)."""
    _code, _status, when = item.split(":", 2)
    return datetime.fromisoformat(when)


async def proposal_of(s: AsyncSession, run_id: UUID, version: int) -> dict[str, Any] | None:
    """The stored proposal JSON of a version."""
    value: Any = (
        await s.execute(
            text("SELECT proposal_json FROM run_cap_versions WHERE run_id = :r AND version = :v"),
            {"r": run_id, "v": version},
        )
    ).scalar_one_or_none()
    return dict(value) if value is not None else None


async def apply_cap(
    s: AsyncSession, run_id: UUID, version: int, cap_micros: int, sample_ops: dict[str, int]
) -> None:
    """Write the approved version to the run's enforcement columns: only forward in version and
    never below the spend already made (K1). ``ApprovalInvalid`` when the condition fails."""
    row = (
        await s.execute(
            text(
                "UPDATE account_runs SET budget_usd = :cap, cap_version = :v,"
                " cap_sample_ops = CAST(:ops AS jsonb)"
                " WHERE id = :r AND (cap_version IS NULL OR cap_version < :v)"
                " AND spent_usd <= :cap RETURNING id"
            ),
            {
                "cap": micros_to_usd(cap_micros),
                "v": version,
                "ops": json.dumps(sample_ops, sort_keys=True),
                "r": run_id,
            },
        )
    ).first()
    if row is None:
        raise ApprovalInvalid("the cap cannot go back a version or below the spend already made")


async def save_snapshots(
    s: AsyncSession, run_id: UUID, phase: str, snapshots: Sequence[BalanceSnapshot]
) -> None:
    """Insert balance snapshots for a phase (``start`` or ``end``)."""
    for snap in snapshots:
        await s.execute(
            text(
                "INSERT INTO balance_snapshots (run_id, phase, provider_code, status, balance,"
                " unit,"
                " source, taken_at) VALUES (:r, :ph, :p, :st, :b, :u, :src, :at)"
            ),
            {
                "r": run_id,
                "ph": phase,
                "p": snap.provider_code,
                "st": snap.status,
                "b": snap.balance,
                "u": snap.unit,
                "src": snap.source[:300],
                "at": snap.taken_at,
            },
        )


async def load_snapshots(s: AsyncSession, run_id: UUID, phase: str) -> tuple[BalanceSnapshot, ...]:
    """Snapshots of a phase, by provider code."""
    rows = (
        await s.execute(
            text(
                "SELECT provider_code, status, balance, unit, source, taken_at FROM"
                " balance_snapshots"
                " WHERE run_id = :r AND phase = :ph ORDER BY provider_code, id"
            ),
            {"r": run_id, "ph": phase},
        )
    ).all()
    return tuple(
        BalanceSnapshot(
            provider_code=r.provider_code,
            status=r.status,
            balance=Decimal(r.balance) if r.balance is not None else None,
            unit=r.unit,
            source=r.source,
            taken_at=r.taken_at,
        )
        for r in rows
    )


async def store_report(
    s: AsyncSession,
    run_id: UUID,
    status: str,
    content_hash: str,
    operator: dict[str, Any],
    tenant: dict[str, Any],
) -> int:
    """Store a report version unless the same content is already stored (duplicate delivery and
    racing finalisation store one row); returns its version."""
    existing: Any = (
        await s.execute(
            text("SELECT version FROM spend_reports WHERE run_id = :r AND content_hash = :h"),
            {"r": run_id, "h": content_hash},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    version: Any = (
        await s.execute(
            text("SELECT COALESCE(max(version), 0) + 1 FROM spend_reports WHERE run_id = :r"),
            {"r": run_id},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO spend_reports (run_id, version, status, content_hash, operator_json,"
            " tenant_json) VALUES (:r, :v, :st, :h, CAST(:op AS jsonb), CAST(:tn AS jsonb))"
        ),
        {
            "r": run_id,
            "v": int(version),
            "st": status,
            "h": content_hash,
            "op": json.dumps(operator, sort_keys=True),
            "tn": json.dumps(tenant, sort_keys=True),
        },
    )
    return int(version)
