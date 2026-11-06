"""Routes that wait for a screen: ``@behind_flag`` (fix prompt B, task 4; ADR-0112).

The route audit of 2026-11-03 (``docs/audits/orphan-routes.md``) found tenant routes no web screen
calls. The connector, CRM and monitoring ones are product work in progress: they stay, but a
client sees them only when its feature flag is on (``feature_flags``, evaluated per client by
``admin/flags.evaluate``: deny by default). With the flag off the route answers exactly what an
unknown path answers (404, the same problem body), so nothing reveals it exists and the route
catalogue's default deny is untouched. The check runs after the route's own dependencies (the
caller is authenticated and authorised first) and before its body.

Usage, below the router decorator so the router registers the guarded function::

    @router.get("/signals", response_model=C.SignalPage)
    @behind_flag("monitoring")
    async def signals(...): ...

GUARANTEE(test_a_gated_route_is_404_until_the_clients_flag_is_on)
GUARANTEE(test_every_route_of_the_gated_modules_carries_its_flag)
"""

from __future__ import annotations

import functools
import inspect
import typing
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import HTTPException, Request

# every gated flag and what it gates (one place: the tests and the audit read it)
GATED_FLAGS: dict[str, str] = {
    "connectors": "/v1/connectors/*: tenant data connectors (Drive, CRM sources), no screen yet",
    "crm": "/v1/crm/*: CRM write-back, no screen yet",
    "monitoring": "/v1/signals, /v1/alerts*, /v1/subscriptions*, /v1/monitoring/budget, "
    "/v1/accounts/{domain}/history: the monitoring product, not wired (fix prompt F)",
}
_REQUEST_PARAM = "_flag_request"
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


async def flag_on(tenant_id: Any, flag: str) -> bool:
    """Whether ``flag`` is on for the client (newest rule; deny by default)."""
    from account_intelligence.db.session import tenant_txn
    from admin import flags
    from metering.limits import plan_code

    async with tenant_txn(tenant_id) as s:
        rule = await flags.current(s, flag)
        if not rule:
            return False
        plan = await plan_code(s)
    return flags.evaluate(rule, flag, tenant_id, plan)


async def require_flag(request: Request, flag: str) -> None:
    """404 (as for an unknown path) unless the authenticated client has ``flag`` on."""
    tenant = getattr(getattr(request.state, "principal", None), "tenant_id", None)
    if tenant is None or not await flag_on(tenant, flag):
        raise HTTPException(status_code=404)


def flag_of(endpoint: Any) -> str | None:
    """The flag an endpoint is gated by (None: not gated)."""
    return getattr(endpoint, "__behind_flag__", None)


def behind_flag(flag: str) -> Callable[[F], F]:
    """Gate a route on a client feature flag (see the module docstring)."""
    if flag not in GATED_FLAGS:
        raise ValueError(f"unknown gated flag {flag!r}: add it to GATED_FLAGS with its scope")

    def wrap(endpoint: F) -> F:
        # FastAPI reads the signature of the registered function and resolves string annotations
        # in ITS module; the endpoint's own (postponed) annotations are resolved here instead
        hints = typing.get_type_hints(endpoint, include_extras=True)
        original = inspect.signature(endpoint)
        params = [
            p.replace(annotation=hints.get(name, p.annotation))
            for name, p in original.parameters.items()
        ]
        # FastAPI hands the Request to ONE parameter only: reuse the endpoint's own if it has one
        own = next((p.name for p in params if p.annotation is Request), None)
        if own is None:
            params.append(
                inspect.Parameter(
                    _REQUEST_PARAM, inspect.Parameter.KEYWORD_ONLY, annotation=Request
                )
            )

        @functools.wraps(endpoint)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            """The endpoint once the flag is checked for this request."""
            request = kwargs[own] if own is not None else kwargs.pop(_REQUEST_PARAM)
            await require_flag(request, flag)
            return await endpoint(*args, **kwargs)

        guarded.__signature__ = original.replace(  # type: ignore[attr-defined]
            parameters=params, return_annotation=hints.get("return", original.return_annotation)
        )
        guarded.__annotations__ = {**hints, **({} if own else {_REQUEST_PARAM: Request})}
        guarded.__behind_flag__ = flag  # type: ignore[attr-defined]
        return typing.cast(F, guarded)

    return wrap
