"""Support access (AD3, D-66; SaaS 3.1 rule 9, A8): a tenant admin grants ONE named operator a
time-boxed, reason-coded grant; ``read`` allows view-as only, ``write`` is a separate, second
grant. The grant is the only way an operator reaches tenant data: its tenant is resolved by the
definer lookup ``support_grant_tenant`` (unrevoked, unexpired, this operator) on every request.
Every support action is a ``support_session_events`` row and a tenant audit row (actor kind
``support``), visible to the tenant. The tenant revokes at any time; expiry is server side.
There is no break-glass path. The one writer of ``support_grants`` and ``support_session_events``.

GUARANTEE(test_support_view_as_is_read_only_audited_and_visible)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR


class SupportRefused(PermissionError):
    """A grant or a support request that must be refused; fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@lru_cache
def policy() -> dict[str, Any]:
    """``config/support.yaml`` (proposal: durations and reason codes)."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "support.yaml").read_text())
    return data


@dataclass(frozen=True)
class Grant:
    """An active grant as the operator plane sees it."""

    id: UUID
    tenant_id: UUID
    operator: str
    scope: str


async def create(
    s: Any,
    *,
    grantee: str,
    scope: str,
    reason_code: str,
    minutes: int,
    granted_by: str,
    now: datetime,
) -> UUID:
    """A new grant (the caller is a tenant admin with a fresh step-up)."""
    cfg = policy()
    if scope not in ("read", "write"):
        raise SupportRefused("unknown_scope")
    if reason_code not in cfg["reason_codes"]:
        raise SupportRefused("unknown_reason")
    if not 1 <= minutes <= int(cfg["max_minutes"][scope]):
        raise SupportRefused("duration_out_of_range")
    gid: UUID = (
        await s.execute(
            text(
                "INSERT INTO support_grants (grantee, scope, reason_code, granted_by, granted_at,"
                " expires_at) VALUES (:g, :sc, :r, :by, :now, :exp) RETURNING id"
            ),
            {
                "g": grantee,
                "sc": scope,
                "r": reason_code,
                "by": granted_by,
                "now": now,
                "exp": now + timedelta(minutes=minutes),
            },
        )
    ).scalar_one()
    return gid


async def revoke(s: Any, grant_id: UUID, *, by: str, now: datetime) -> bool:
    """End a grant now; True when this call ended it."""
    row = (
        await s.execute(
            text(
                "UPDATE support_grants SET revoked_at = :now, revoked_by = :by WHERE id = :i"
                " AND revoked_at IS NULL RETURNING id"
            ),
            {"now": now, "by": by, "i": grant_id},
        )
    ).first()
    return row is not None


async def listing(s: Any) -> list[dict[str, Any]]:
    """The tenant's grants (newest first)."""
    rows = (
        await s.execute(
            text(
                "SELECT id, grantee, scope, reason_code, granted_by, granted_at, expires_at,"
                " revoked_at"
                " FROM support_grants ORDER BY granted_at DESC LIMIT 200"
            )
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "grantee": r.grantee,
            "scope": r.scope,
            "reason": r.reason_code,
            "granted_by": r.granted_by,
            "granted_at": r.granted_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        }
        for r in rows
    ]


async def resolve(plain: Any, grant_id: UUID, operator: str, now: datetime) -> Grant:
    """The ACTIVE grant to this operator (SupportRefused ``no_active_grant`` otherwise). Runs
    without a tenant context: the definer lookup returns the tenant and scope only."""
    row = (
        await plain.execute(
            text("SELECT tenant_id, scope FROM support_grant_tenant(:g, :o, :now)"),
            {"g": grant_id, "o": operator, "now": now},
        )
    ).first()
    if row is None:
        raise SupportRefused("no_active_grant")
    return Grant(grant_id, row.tenant_id, operator, row.scope)


async def record(
    s: Any,
    grant: Grant,
    *,
    method: str,
    route: str,
    outcome: str,
    request_id: str | None,
    now: datetime,
) -> None:
    """One support action, visible to the tenant."""
    await s.execute(
        text(
            "INSERT INTO support_session_events (grant_id, operator, method, route, outcome,"
            " request_id, at) VALUES (:g, :o, :m, :r, :out, :q, :now)"
        ),
        {
            "g": grant.id,
            "o": grant.operator,
            "m": method,
            "r": route[:200],
            "out": outcome,
            "q": request_id,
            "now": now,
        },
    )
