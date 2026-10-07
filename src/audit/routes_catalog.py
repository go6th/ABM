"""From a catalogued route to its audit event (AU1).

Every route's audit class is in the route catalog (``api.policy``): ``read`` routes are recorded by
the router guard through ``read_event`` (deduplicated), and ``run``, ``export`` and ``admin``
routes record their own event with ``action_event`` inside the action's transaction. The action
name and resource are derived from the route TEMPLATE and the path parameters only, never from a
body, a query or content. Pure.

GUARANTEE(test_every_read_route_emits_an_audit_event)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from audit.events import ActorKind, AuditClass, AuditEvent, Outcome

_PARAM = re.compile(r"^\{(\w+?)(?:_id)?(?::\w+)?\}$")
_WORD = re.compile(r"[^a-z]+")


def resource_of(template: str, params: Mapping[str, object]) -> tuple[str, str, str | None]:
    """``(slug, resource_type, resource_id)`` for a route template: the slug joins the static
    segments after the version; the resource is the LAST path parameter (``run_id`` -> ``run``)
    or, for a collection route, its last static segment."""
    segments = [s for s in template.split("/") if s and s not in ("v1", "operator")]
    static = [_WORD.sub("_", s.lower()).strip("_") for s in segments if not s.startswith("{")]
    slug = "_".join(x for x in static if x)[:60] or "root"
    last_param = next((s for s in reversed(segments) if s.startswith("{")), None)
    if last_param is None:
        return slug, (static[-1] if static else "root")[:40], None
    m = _PARAM.match(last_param)
    name = m.group(1) if m else "resource"
    full = last_param.strip("{}").split(":", 1)[0]
    value = params.get(full)
    return (
        slug,
        _WORD.sub("_", name.lower())[:40] or "resource",
        (None if value is None else str(value)[:300]),
    )


def read_event(
    *,
    template: str,
    params: Mapping[str, object],
    actor: str,
    actor_kind: ActorKind,
    at: datetime,
    request_id: str | None,
    support_grant_id: str | None = None,
) -> AuditEvent:
    """The read event of one request to a ``read`` route."""
    slug, rtype, rid = resource_of(template, params)
    return AuditEvent(
        at=at,
        actor=actor,
        actor_kind=actor_kind,
        audit_class="read",
        action=f"read.{slug}",
        resource_type=rtype,
        resource_id=rid,
        route=template,
        request_id=request_id,
        support_grant_id=support_grant_id,
    )


def action_event(
    *,
    audit_class: AuditClass,
    action: str,
    resource_type: str,
    resource_id: str | None,
    actor: str,
    actor_kind: ActorKind,
    at: datetime,
    route: str | None,
    request_id: str | None,
    outcome: Outcome = "ok",
    support_grant_id: str | None = None,
) -> AuditEvent:
    """The event a run, export or admin handler records in its own transaction."""
    return AuditEvent(
        at=at,
        actor=actor,
        actor_kind=actor_kind,
        audit_class=audit_class,
        action=f"{audit_class}.{action}",
        resource_type=resource_type,
        resource_id=resource_id,
        route=route,
        request_id=request_id,
        outcome=outcome,
        support_grant_id=support_grant_id,
    )
