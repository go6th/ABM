"""Default-deny role policy for the registered v1 API.

Guarantee: every versioned route is covered
(GUARANTEE(test_every_versioned_route_has_an_explicit_policy)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "seller", "viewer"]
PrincipalType = Literal["jwt", "api_key", "operator_support"]


@dataclass(frozen=True)
class RoutePolicy:
    """Explicit role and principal-type allow-list for one versioned route."""

    roles: frozenset[Role]
    principal_types: frozenset[PrincipalType]


ROUTE_POLICIES: dict[tuple[str, str], RoutePolicy] = {
    ("POST", "/v1/runs"): RoutePolicy(
        roles=frozenset({"admin", "seller"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("GET", "/v1/runs/{run_id}"): RoutePolicy(
        roles=frozenset({"admin", "seller", "viewer"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/approve"): RoutePolicy(
        roles=frozenset({"admin", "seller"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/cancel"): RoutePolicy(
        roles=frozenset({"admin", "seller"}),
        principal_types=frozenset({"jwt"}),
    ),
    # Cap decisions move spend, so only admins may take them (ADR-0010 precedence).
    ("POST", "/v1/runs/{run_id}/cap/approve"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/cap/reject"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/cap/extend"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("GET", "/v1/runs/{run_id}/spend"): RoutePolicy(
        roles=frozenset({"admin", "seller", "viewer"}),
        principal_types=frozenset({"jwt"}),
    ),
    # Credit manager v2: live spend for every tenant role; pause decisions move money, so admins
    # with a fresh step-up only (M5).
    ("GET", "/v1/runs/{run_id}/spend/live"): RoutePolicy(
        roles=frozenset({"admin", "seller", "viewer"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/cap/pauses/{request_id}/approve"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"jwt"}),
    ),
    ("POST", "/v1/runs/{run_id}/cap/pauses/{request_id}/decline"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"jwt"}),
    ),
    # Operator plane (fail closed until D-33 support sessions exist): uploads are operator only.
    ("GET", "/operator/v1/runs/{run_id}/spend/live"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"operator_support"}),
    ),
    ("POST", "/operator/v1/runs/{run_id}/prepulled/intents"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"operator_support"}),
    ),
    ("POST", "/operator/v1/runs/{run_id}/prepulled/{intent_id}/complete"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"operator_support"}),
    ),
    ("GET", "/operator/v1/runs/{run_id}/prepulled/{intent_id}"): RoutePolicy(
        roles=frozenset({"admin"}),
        principal_types=frozenset({"operator_support"}),
    ),
}


def policy_for(method: str, path: str) -> RoutePolicy | None:
    """Return the explicit policy for a route, or None to deny by default."""
    return ROUTE_POLICIES.get((method.upper(), path))
