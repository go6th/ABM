"""Router guard for ``/v1`` and ``/operator/v1``: the route catalog is enforced at runtime (TN2).

The guard runs as a router-level dependency, before any route dependency or handler. It finds the
matched route's template in ``api.policy.ROUTE_POLICIES`` and refuses with 403 when there is none,
so a route added without a catalog entry is unreachable rather than open. The matched policy is
left on ``request.state.route_policy`` for the principal resolver and the audit writer.

GUARANTEE(test_unregistered_route_is_refused_by_the_guard)
GUARANTEE(test_csrf_is_required_on_every_state_changing_cookie_call)
"""

from __future__ import annotations

import re

from fastapi import Depends, HTTPException, Request
from starlette.routing import Route

from account_intelligence.api.auth import Identity, check_membership, identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.policy import RoutePolicy, policy_for
from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.api.tenant_plane import CSRF_KEY_REF, TenantPlane, plane_of
from account_intelligence.platform.ports import NotConfigured
from identity.csrf import csrf_ok, needs_csrf
from identity.roles import needs_step_up
from identity.sessions import step_up_fresh

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


async def authorize(request: Request, ident: Identity = Depends(identity)) -> Identity:
    """Second router dependency on authenticated tenant routes (after ``route_guard``):

    * the caller's role must be in the route's catalog roles (403);
    * membership is re-checked through the cache (401, ID2);
    * a cookie-authenticated unsafe method must carry this session's CSRF token (403, ID3);
    * an action that needs step-up needs an MFA sign-in within the window (403, ID3).

    The authorised identity is left on ``request.state.principal``."""
    policy: RoutePolicy | None = getattr(request.state, "route_policy", None)
    if policy is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if "anonymous" in policy.principal_types:
        raise HTTPException(status_code=403, detail="forbidden")
    if ident.kind not in policy.principal_types or ident.role not in policy.roles:
        raise HTTPException(status_code=403, detail="forbidden")
    await check_membership(request, ident)
    plane = plane_of(request)
    if ident.kind == "session" and needs_csrf(request.method):
        key = await _key(plane, CSRF_KEY_REF)
        presented = request.headers.get(plane.policy.csrf_header)
        if ident.session_id is None or not csrf_ok(key, ident.session_id, presented):
            raise APIProblem(
                403,
                "csrf_refused",
                "Request refused",
                "This request needs the session's CSRF token.",
            )
    stale = not step_up_fresh(ident.step_up_at, plane.clock.now(), plane.policy)
    if policy.action is not None and needs_step_up(policy.action) and stale:
        raise APIProblem(
            403,
            "step_up_required",
            "Step-up required",
            "Sign in again with multi-factor authentication to do this.",
        )
    request.state.principal = ident
    return ident


async def _key(plane: TenantPlane, ref: str) -> bytes:
    try:
        return await plane.key(ref)
    except NotConfigured as e:
        raise DependencyUnavailable() from e
