"""The operator principal for ``/operator/v1`` routes (6th staff acting on a tenant's run).

The operator plane (audited, time-boxed support sessions, decision D-33) is not built yet, so the
production dependency fails closed with 503 and no operator route can act; tests and a future
adapter supply the principal through FastAPI dependency overrides. The tenant still comes only
from the verified session, never from a body, a path or a query (W3).

GUARANTEE(test_operator_routes_fail_closed_without_the_operator_plane): no fake operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from account_intelligence.api.problem import DependencyUnavailable


@dataclass(frozen=True)
class OperatorIdentity:
    """A 6th operator inside an audited support session for one tenant."""

    operator_id: str
    tenant_id: UUID
    support_session_id: str
    step_up_at: datetime | None = None


async def require_operator() -> OperatorIdentity:
    """Production: the operator plane does not exist yet (D-33), so refuse (503)."""
    raise DependencyUnavailable()
