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
}


def policy_for(method: str, path: str) -> RoutePolicy | None:
    """Return the explicit policy for a route, or None to deny by default."""
    return ROUTE_POLICIES.get((method.upper(), path))
