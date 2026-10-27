"""Signals, alerts and monitoring (Phase 4; ``/v1``). Tenant plane only.

* ``GET /v1/signals?cursor=``: the Open Door read API (``signal_page.v1``, D-78).
* ``GET /v1/alerts``: the caller's alert inbox; ``POST /v1/alerts/{alert_id}/feedback``.
* ``GET /v1/alerts/usefulness``: useful / rated with its base (admins).
* ``GET``/``PUT /v1/subscriptions``: the caller's own settings (subscribe up);
  ``POST /v1/subscriptions/subscribe-all``: an explicit, audited bulk action.
* ``GET /v1/monitoring/budget``: the monitoring allowance used, in percent only (admins).
* ``GET /v1/accounts/{domain}/history``: expired signals of an account.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.accounts import exclusion_rule
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from alerts import feedback, rules, subscriptions
from governance import monitoring_budget
from opendoor import api as opendoor
from refresh import history

router = APIRouter(tags=["signals"])
register("GET", "/v1/signals", tenant_route("read_public", "read"))
register("GET", "/v1/alerts", tenant_route("read_public", "read"))
register("POST", "/v1/alerts/{alert_id}/feedback", tenant_route("give_feedback", "none"))
register("GET", "/v1/alerts/usefulness", tenant_route("read_usage", "read"))
register("GET", "/v1/subscriptions", tenant_route("read_public", "read"))
register("PUT", "/v1/subscriptions", tenant_route("give_feedback", "admin"))
register("POST", "/v1/subscriptions/subscribe-all", tenant_route("give_feedback", "admin"))
register("GET", "/v1/monitoring/budget", tenant_route("read_usage", "read"))
register("GET", "/v1/accounts/{domain}/history", tenant_route("read_public", "read"))


@router.get("/signals", response_model=C.SignalPage)
async def signals(
    request: Request,
    cursor: str | None = Query(default=None, max_length=200),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One page of signal events."""
    try:
        async with tenant_txn(ident.tenant_id) as s:
            return await opendoor.page(s, cursor)
    except ValueError:
        raise HTTPException(422, "bad cursor") from None


@router.get("/alerts", response_model=C.Alerts)
async def inbox(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's alerts (newest first)."""
    async with tenant_txn(ident.tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT a.id, a.rule_id, a.tier, a.lead, a.subject, a.body,"
                    " a.created_at"
                    " FROM alerts a JOIN alert_outbox o ON o.alert_id = a.id WHERE o.recipient = :u"
                    " ORDER BY a.created_at DESC, a.id LIMIT 200"
                ),
                {"u": ident.user_id},
            )
        ).all()
    return {
        "alerts": [
            {
                "id": str(r.id),
                "rule": r.rule_id,
                "tier": r.tier,
                "lead": r.lead,
                "subject": r.subject,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


class Rating(BaseModel):
    """A rating with a fixed reason."""

    model_config = ConfigDict(extra="forbid")
    verdict: Literal["useful", "not_useful"]
    reason: str = Field(pattern=r"^[a-z_]{1,40}$")


@router.post("/alerts/{alert_id}/feedback", response_model=C.AlertRated, status_code=201)
async def rate(
    alert_id: UUID, body: Rating, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Rate one of the caller's alerts."""
    async with tenant_txn(ident.tenant_id) as s:
        mine = (
            await s.execute(
                text("SELECT 1 FROM alert_outbox WHERE alert_id = :a AND recipient = :u LIMIT 1"),
                {"a": alert_id, "u": ident.user_id},
            )
        ).first()
        if mine is None:
            raise HTTPException(404, "not found")
        try:
            await feedback.rate(
                s,
                alert_id=alert_id,
                user_id=ident.user_id,
                verdict=body.verdict,
                reason=body.reason,
                now=plane_of(request).clock.now(),
            )
        except ValueError:
            raise HTTPException(422, "unknown reason") from None
    return {"alert_id": str(alert_id), "verdict": body.verdict}


@router.get("/alerts/usefulness", response_model=C.AlertUsefulness)
async def usefulness(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The usefulness metric with its base and the target (never as a result)."""
    async with tenant_txn(ident.tenant_id) as s:
        return await feedback.metric(s)


class Settings(BaseModel):
    """The caller's own alert settings."""

    model_config = ConfigDict(extra="forbid")
    digest: bool = True
    immediate_rules: list[str] = Field(default_factory=list, max_length=20)
    slack: bool = False
    threshold: int = Field(default=0, ge=0, le=1000)
    muted_accounts: list[str] = Field(default_factory=list, max_length=500)


def _out(sub: subscriptions.Subscription) -> dict[str, Any]:
    return {
        "digest": sub.digest,
        "immediate_rules": sorted(sub.immediate_rules),
        "slack": sub.slack,
        "threshold": sub.threshold,
        "muted_accounts": sorted(sub.muted_accounts),
    }


@router.get("/subscriptions", response_model=C.Subscription)
async def my_settings(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The caller's settings (the digest-only defaults when none were saved)."""
    async with tenant_txn(ident.tenant_id) as s:
        return _out(await subscriptions.current(s, ident.user_id))


@router.put("/subscriptions", response_model=C.Subscription)
async def save_settings(
    body: Settings, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Save the caller's settings (rules must exist; accounts must be the tenant's)."""
    known = set(rules.load())
    if set(body.immediate_rules) - known:
        raise HTTPException(422, "unknown rule")
    async with tenant_txn(ident.tenant_id) as s:
        if body.muted_accounts:
            own = {
                str(r.id)
                for r in (
                    await s.execute(
                        text("SELECT id FROM accounts WHERE id = ANY(CAST(:ids AS uuid[]))"),
                        {"ids": body.muted_accounts},
                    )
                ).all()
            }
            if own != set(body.muted_accounts):
                raise HTTPException(404, "not found")
        sub = subscriptions.Subscription(
            body.digest,
            frozenset(body.immediate_rules),
            body.slack,
            body.threshold,
            frozenset(body.muted_accounts),
        )
        await subscriptions.save(
            s,
            ident.user_id,
            sub,
            set_by=ident.user_id,
            bulk=False,
            now=plane_of(request).clock.now(),
        )
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="subscriptions_save",
            resource_type="subscription",
            resource_id=ident.user_id,
        )
    return _out(sub)


@router.post("/subscriptions/subscribe-all", response_model=C.Subscription)
async def subscribe_all(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Subscribe the caller to every immediate rule: explicit and audited."""
    async with tenant_txn(ident.tenant_id) as s:
        cur = await subscriptions.current(s, ident.user_id)
        sub = subscriptions.Subscription(
            cur.digest,
            frozenset(r.id for r in rules.load().values() if r.tier == "immediate"),
            cur.slack,
            cur.threshold,
            cur.muted_accounts,
        )
        await subscriptions.save(
            s,
            ident.user_id,
            sub,
            set_by=ident.user_id,
            bulk=True,
            now=plane_of(request).clock.now(),
        )
        await audit_action(
            s,
            request,
            audit_class="admin",
            action="subscriptions_bulk",
            resource_type="subscription",
            resource_id=ident.user_id,
        )
    return _out(sub)


@router.get("/monitoring/budget", response_model=C.MonitoringBudget)
async def budget(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The monitoring allowance used this month, in percent only."""
    async with tenant_txn(ident.tenant_id) as s:
        plan = (
            await s.execute(
                text("SELECT plan_code FROM plan_assignments ORDER BY version DESC LIMIT 1")
            )
        ).first()
        return await monitoring_budget.tenant_view(
            s, plan.plan_code if plan else "pilot", plane_of(request).clock.now()
        )


@router.get("/accounts/{domain}/history", response_model=C.History)
async def account_history(
    domain: str, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """An account's expired signals (newest first)."""
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        if rule.excludes_company(domain):
            raise HTTPException(404, "not found")
        acc = (
            await s.execute(
                text("SELECT id FROM accounts WHERE domain = :d AND deleted_at IS NULL"),
                {"d": domain},
            )
        ).first()
        if acc is None:
            raise HTTPException(404, "not found")
        return {"history": await history.listing(s, acc.id)}
