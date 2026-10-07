"""Default-deny route catalog for the v1 tenant plane and the operator plane (TN2, AU1).

Every route under ``/v1`` and ``/operator/v1`` has exactly one entry: the roles that may call it,
the principal types it accepts, its audit class (``read``, ``run``, ``export``, ``admin`` or
``none``) and where its tenant comes from. A route without an entry is refused with 403 by the
router guard (``api.guard``) before any handler runs, and ``scripts/check_route_catalog.py``
fails the build when a route has no entry or an entry has no route.

The tenant is never a request field: ``session`` means the verified session or token,
``operator_grant`` means an operator's tenant-admin support grant (D-66), ``none`` means the
route reads no tenant data (an IdP callback resolves the tenant from the tenant's own IdP
connection, never from a claim the IdP supplies).

Guarantees: every versioned route is covered
(GUARANTEE(test_every_versioned_route_has_an_explicit_policy)); an absent entry is a 403
(GUARANTEE(test_unregistered_route_is_refused_by_the_guard)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "analyst", "seller", "viewer"]
PrincipalType = Literal["jwt", "session", "api_key", "operator_support", "anonymous"]
AuditClass = Literal["read", "run", "export", "admin", "none"]
TenantSource = Literal["session", "operator_grant", "none"]

ALL_ROLES: frozenset[Role] = frozenset({"admin", "analyst", "seller", "viewer"})
TENANT_PRINCIPALS: frozenset[PrincipalType] = frozenset({"jwt", "session"})
OPERATOR: frozenset[PrincipalType] = frozenset({"operator_support"})


@dataclass(frozen=True)
class RoutePolicy:
    """Explicit role and principal-type allow-list, audit class and tenant source of one route.

    ``action`` names the role-policy action (``identity.roles``) the roles were derived from;
    routes that predate the role policy carry explicit roles and ``action=None``."""

    roles: frozenset[Role]
    principal_types: frozenset[PrincipalType]
    audit: AuditClass
    tenant_source: TenantSource
    action: str | None = None


def _tenant(roles: set[Role], audit: AuditClass) -> RoutePolicy:
    return RoutePolicy(frozenset(roles), TENANT_PRINCIPALS, audit, "session")


def _operator(audit: AuditClass) -> RoutePolicy:
    return RoutePolicy(frozenset({"admin"}), OPERATOR, audit, "operator_grant")


_SELLERS: set[Role] = {"admin", "analyst", "seller"}
_EVERYONE: set[Role] = set(ALL_ROLES)

ROUTE_POLICIES: dict[tuple[str, str], RoutePolicy] = {
    ("POST", "/v1/runs"): _tenant(_SELLERS, "run"),
    ("GET", "/v1/runs/{run_id}"): _tenant(_EVERYONE, "read"),
    ("POST", "/v1/runs/{run_id}/approve"): _tenant(_SELLERS, "run"),
    ("POST", "/v1/runs/{run_id}/cancel"): _tenant(_SELLERS, "run"),
    # Cap decisions move spend, so only admins may take them (ADR-0010 precedence).
    ("POST", "/v1/runs/{run_id}/cap/approve"): _tenant({"admin"}, "run"),
    ("POST", "/v1/runs/{run_id}/cap/reject"): _tenant({"admin"}, "run"),
    ("POST", "/v1/runs/{run_id}/cap/extend"): _tenant({"admin"}, "run"),
    ("GET", "/v1/runs/{run_id}/spend"): _tenant(_EVERYONE, "read"),
    # Credit manager v2: live spend for every tenant role; pause decisions move money, so admins
    # with a fresh step-up only (M5).
    ("GET", "/v1/runs/{run_id}/spend/live"): _tenant(_EVERYONE, "read"),
    ("POST", "/v1/runs/{run_id}/cap/pauses/{request_id}/approve"): _tenant({"admin"}, "run"),
    ("POST", "/v1/runs/{run_id}/cap/pauses/{request_id}/decline"): _tenant({"admin"}, "run"),
    # Operator plane: every read of tenant data goes through a support grant (AD1, D-66).
    ("GET", "/operator/v1/runs/{run_id}/spend/live"): _operator("read"),
    ("POST", "/operator/v1/runs/{run_id}/prepulled/intents"): _operator("admin"),
    ("POST", "/operator/v1/runs/{run_id}/prepulled/{intent_id}/complete"): _operator("admin"),
    ("GET", "/operator/v1/runs/{run_id}/prepulled/{intent_id}"): _operator("read"),
    # Observability (O15): operator reads only; tenants never.
    ("GET", "/operator/v1/runs/{run_id}/timeline"): _operator("read"),
    ("GET", "/operator/v1/ops/cost/{run_id}"): _operator("read"),
    ("GET", "/operator/v1/ops/failures"): _operator("read"),
    ("GET", "/operator/v1/ops/stuck"): _operator("read"),
}


def tenant_route(action: str, audit: AuditClass, *, step_up: bool = True) -> RoutePolicy:
    """A tenant route whose roles come from the role policy for ``action`` (D-61, one source).
    ``step_up=False`` keeps the roles but never asks for step-up (a read that shares an
    administrative action's roles)."""
    from identity.roles import roles_for

    roles = frozenset(r for r in roles_for(action) if r in ALL_ROLES)
    return RoutePolicy(
        roles,
        TENANT_PRINCIPALS,
        audit,
        "session",
        action if step_up else None,
    )


ANONYMOUS = RoutePolicy(frozenset(), frozenset({"anonymous"}), "none", "none")


def register(method: str, path: str, policy: RoutePolicy) -> None:
    """Add one entry at import time of the module that defines the route (a duplicate is a
    programming error, never an override)."""
    key = (method.upper(), path)
    if key in ROUTE_POLICIES:
        raise ValueError(f"route policy registered twice: {key}")
    ROUTE_POLICIES[key] = policy


def policy_for(method: str, path: str) -> RoutePolicy | None:
    """Return the explicit policy for a route, or None to deny by default."""
    return ROUTE_POLICIES.get((method.upper(), path))
