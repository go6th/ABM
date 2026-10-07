"""Router guard for ``/v1`` and ``/operator/v1``: the route catalog is enforced at runtime (TN2).

The guard runs as a router-level dependency, before any route dependency or handler. It finds the
matched route's template in ``api.policy.ROUTE_POLICIES`` and refuses with 403 when there is none,
so a route added without a catalog entry is unreachable rather than open. The matched policy is
left on ``request.state.route_policy`` for the principal resolver and the audit writer.

GUARANTEE(test_unregistered_route_is_refused_by_the_guard)
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Request
from starlette.routing import Route

from account_intelligence.api.policy import RoutePolicy, policy_for

_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")


def route_template(request: Request) -> str | None:
    """The matched route's full path template (``/v1/runs/{run_id}``), never the raw path.

    FastAPI includes routers lazily, so ``scope["route"].path_format`` is relative to the
    innermost router. The full template is taken from FastAPI's effective route context when it
    is present; otherwise it is rebuilt from the request path and the matched suffix (router
    prefixes in this application carry no path parameters, which the route catalog check
    enforces)."""
    effective = request.scope.get("fastapi", {}).get("effective_route_context")
    full = getattr(effective, "path_format", None)
    if isinstance(full, str):
        return full
    route = request.scope.get("route")
    if not isinstance(route, Route):
        return None
    params = request.scope.get("path_params", {})
    rendered = _PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), route.path_format)
    path: str = request.scope.get("path", "")
    if not path.endswith(rendered):
        return None
    return path[: len(path) - len(rendered)] + route.path_format


def matched_policy(request: Request) -> RoutePolicy | None:
    """The catalog entry for this request's method and route template (HEAD reads as GET)."""
    template = route_template(request)
    if template is None:
        return None
    method = "GET" if request.method == "HEAD" else request.method
    return policy_for(method, template)


async def route_guard(request: Request) -> None:
    """Refuse (403) any request whose route has no catalog entry."""
    policy = matched_policy(request)
    if policy is None:
        raise HTTPException(status_code=403, detail="forbidden")
    request.state.route_policy = policy
