"""Off limits until cleared (CN2): items that surface outside the allow-list, with unknown
ancestry, or as a shortcut pointing outside, are recorded HELD and never read; a tenant admin
clears (or refuses) each one, once. Cleared items may be fetched by a later pass. The one writer
of ``connector_quarantine``.

GUARANTEE(test_a_fetch_pass_is_bounded_resumable_logged_and_read_only)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text


class QuarantineError(ValueError):
    """A decision that is not allowed (already decided, unknown item)."""


async def hold(
    s: Any, *, connection_id: UUID, resource_id: str, resource_kind: str, reason: str, now: datetime
) -> bool:
    """Record an item off limits (idempotent); True when it is newly held."""
    row = (
        await s.execute(
            text(
                "INSERT INTO connector_quarantine (connection_id, resource_id, resource_kind,"
                " reason, first_seen_at) VALUES (:c, :r, :k, :why, :now)"
                " ON CONFLICT (tenant_id, connection_id, resource_id) DO NOTHING RETURNING id"
            ),
            {"c": connection_id, "r": resource_id, "k": resource_kind, "why": reason, "now": now},
        )
    ).first()
    return row is not None


async def status(s: Any, connection_id: UUID, resource_id: str) -> str | None:
    """``held``, ``cleared``, ``refused`` or None (never surfaced)."""
    found: str | None = (
        await s.execute(
            text(
                "SELECT status FROM connector_quarantine WHERE connection_id = :c"
                " AND resource_id = :r"
            ),
            {"c": connection_id, "r": resource_id},
        )
    ).scalar_one_or_none()
    return found


async def decide(
    s: Any, *, item_id: UUID, connection_id: UUID, decision: str, actor: str, now: datetime
) -> str:
    """Clear or refuse a held item, once; returns its resource id."""
    if decision not in ("cleared", "refused"):
        raise QuarantineError("decision must be cleared or refused")
    row = (
        await s.execute(
            text(
                "UPDATE connector_quarantine SET status = :d, decided_by = :a, decided_at = :now"
                " WHERE id = :i AND connection_id = :c AND status = 'held' RETURNING resource_id"
            ),
            {"d": decision, "a": actor, "now": now, "i": item_id, "c": connection_id},
        )
    ).first()
    if row is None:
        raise QuarantineError("no such held item")
    return str(row.resource_id)


async def cleared(s: Any, connection_id: UUID) -> list[str]:
    """Resource ids an admin cleared (a later pass may read them)."""
    rows = (
        await s.execute(
            text(
                "SELECT resource_id FROM connector_quarantine WHERE connection_id = :c"
                " AND status = 'cleared' ORDER BY resource_id"
            ),
            {"c": connection_id},
        )
    ).all()
    return [str(r.resource_id) for r in rows]


async def listing(s: Any, connection_id: UUID) -> list[dict[str, Any]]:
    """Every surfaced item of a connection (identifiers and reasons only)."""
    rows = (
        await s.execute(
            text(
                "SELECT id, resource_id, resource_kind, reason, status FROM connector_quarantine"
                " WHERE connection_id = :c ORDER BY first_seen_at, resource_id LIMIT 1000"
            ),
            {"c": connection_id},
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "resource_id": r.resource_id,
            "kind": r.resource_kind,
            "reason": r.reason,
            "status": r.status,
        }
        for r in rows
    ]
