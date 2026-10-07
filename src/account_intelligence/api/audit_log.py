"""The API's audit writers (AU1): the read event the router guard records, and the helper run,
export and admin handlers call inside their own transaction (the action and its audit row commit
together, so an audit write failure fails the action: fail closed).

GUARANTEE(test_run_export_and_admin_handlers_record_their_audit_event)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.api.guard import route_template
from account_intelligence.api.tenant_plane import plane_of
from audit.events import AuditEvent, dedupe_key, load_policy, record
from audit.routes_catalog import action_event, read_event


def _actor(request: Request) -> tuple[str, Literal["user", "operator", "support"], str | None]:
    ident: Any = getattr(request.state, "principal", None)
    grant = getattr(request.state, "support_grant_id", None)
    if grant is not None:
        return str(getattr(ident, "user_id", "support")), "support", str(grant)
    if ident is None:
        return "unknown", "user", None
    return str(ident.user_id), "user", None


def read_audit_event(request: Request) -> tuple[AuditEvent, str]:
    """The read event for this request and its dedupe key."""
    actor, kind, grant = _actor(request)
    event = read_event(
        template=route_template(request) or request.url.path,
        params=dict(request.path_params),
        actor=actor,
        actor_kind=kind,
        at=plane_of(request).clock.now(),
        request_id=getattr(request.state, "request_id", None),
        support_grant_id=grant,
    )
    return event, dedupe_key(event, load_policy().read_dedupe_window_s)


async def audit_action(
    s: AsyncSession,
    request: Request,
    *,
    audit_class: Literal["run", "export", "admin"],
    action: str,
    resource_type: str,
    resource_id: object | None,
    outcome: Literal["ok", "denied", "error"] = "ok",
) -> int | None:
    """Record this request's run, export or admin event in the caller's transaction."""
    actor, kind, grant = _actor(request)
    return await record(
        s,
        action_event(
            audit_class=audit_class,
            action=action,
            resource_type=resource_type,
            resource_id=None if resource_id is None else str(resource_id),
            actor=actor,
            actor_kind=kind,
            at=plane_of(request).clock.now(),
            route=route_template(request),
            request_id=getattr(request.state, "request_id", None),
            outcome=outcome,
            support_grant_id=grant,
        ),
    )


def audited_txn(
    request: Request,
    *,
    audit_class: Literal["run", "export", "admin"],
    action: str,
    resource_type: str,
    resource_id: object | None,
) -> Any:
    """A ``tenant_txn``-shaped factory for services that open their own transaction: the FIRST
    transaction that completes without an exception also records the event, before it commits
    (one event per request; an audit failure rolls the action back)."""
    from contextlib import asynccontextmanager

    from account_intelligence.db.session import tenant_txn

    state = {"done": False}

    @asynccontextmanager
    async def factory(tenant_id: Any, **kw: Any) -> Any:
        """``tenant_txn`` that records the request's event in the first completed transaction."""
        async with tenant_txn(tenant_id, **kw) as s:
            yield s
            if not state["done"] and not kw.get("snapshot"):
                await audit_action(
                    s,
                    request,
                    audit_class=audit_class,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                state["done"] = True

    return factory


async def audit_refusal(
    request: Request,
    tenant_id: Any,
    *,
    audit_class: Literal["run", "export", "admin"],
    action: str,
    resource_type: str,
    resource_id: object | None,
) -> None:
    """Record a refused action (outcome ``denied``) in a transaction of its own."""
    from account_intelligence.db.session import tenant_txn

    async with tenant_txn(tenant_id) as s:
        await audit_action(
            s,
            request,
            audit_class=audit_class,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
        )
