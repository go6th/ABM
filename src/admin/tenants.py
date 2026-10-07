"""Tenant management (AD1, AD2): create, overview (identifiers, states and counts only, through
the definer lookup ``operator_tenant_overview``), suspend, reactivate and deletion requests through
``tenancy.lifecycle`` (append-only events, the two-person deletion cascade). Every operator action
is an ``operator_audit`` row. The one writer of ``operator_audit``.

GUARANTEE(test_the_operator_plane_manages_tenants_without_reading_content)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text


async def audit(
    plain: Any,
    *,
    operator: str,
    action: str,
    target: str | None,
    reason_code: str | None,
    request_id: str | None,
    now: datetime,
) -> None:
    """One operator-plane audit row (platform table; identifiers only)."""
    await plain.execute(
        text(
            "INSERT INTO operator_audit (operator, action, target, reason_code, request_id, at)"
            " VALUES (:o, :a, :t, :r, :q, :now)"
        ),
        {"o": operator, "a": action, "t": target, "r": reason_code, "q": request_id, "now": now},
    )


async def overview(plain: Any) -> list[dict[str, Any]]:
    """Every tenant's metadata (no names, no content)."""
    rows = (await plain.execute(text("SELECT * FROM operator_tenant_overview()"))).all()
    return [
        {
            "tenant_id": str(r.tenant_id),
            "created_at": r.created_at.isoformat(),
            "plan": r.plan,
            "suspended": bool(r.suspended),
            "members": int(r.members),
            "accounts": int(r.accounts),
            "live_runs": int(r.live_runs),
        }
        for r in rows
    ]


async def create(plain: Any, *, name: str) -> UUID:
    """A new tenant with an empty wallet, through the definer function ``provision_tenant`` (the
    runtime role has no insert on the tenant registry)."""
    tid: UUID = (await plain.execute(text("SELECT provision_tenant(:n)"), {"n": name})).scalar_one()
    return tid
