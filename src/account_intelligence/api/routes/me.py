"""Current tenant identity and the backend's authorization/capability view."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.health import LLM_MISSING, llm_state
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.settings import accounts_used, client_settings, touch_activity
from admin import flags
from identity import roles, sessions
from metering import limits

router = APIRouter(tags=["identity"])
register("GET", "/v1/me", tenant_route("read_public", "read", step_up=False))

CAPABILITIES: dict[str, str] = {
    "read_public": "brief:public:read",
    "read_internal": "brief:internal:read",
    "start_run": "runs:create",
    "cancel_run": "runs:cancel",
    "approve_run": "runs:approve",
    "configure_alerts": "alerts:manage",
    "save_own_view": "views:save",
    "edit_shared_views": "views:share",
    "edit_tracker_notes": "tracker:notes:write",
    "give_feedback": "feedback:write",
    "revoke_own_session": "sessions:revoke:self",
    "manage_own_auth": "auth:manage:self",
    "approve_cap": "wallet:cap:approve",
    "manage_users": "users:manage",
    "manage_sso": "sso:manage",
    "manage_settings": "settings:manage",
    "manage_connectors": "connectors:manage",
    "read_audit": "audit:read",
    "export_audit": "audit:export",
    "read_usage": "usage:read",
    "grant_support": "support:grant",
    "request_deletion": "tenant:delete:request",
}


@router.get("/me", response_model=C.Me)
async def current_user(request: Request, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Return only the active session's tenant and capability metadata. Every page of the
    workspace loads this, so it also records the user's activity (idle expiry counts from it)."""
    async with tenant_txn(ident.tenant_id) as s:
        await touch_activity(s, ident.user_id, plane_of(request).clock.now())
        row = (
            await s.execute(
                text(
                    "SELECT t.name, t.plan, t.isolation_tier, u.email, u.display_name"
                    " FROM tenants t LEFT JOIN users u"
                    " ON u.tenant_id = t.id AND u.subject = :subject"
                    " WHERE t.id = :tenant_id"
                ),
                {"subject": ident.user_id, "tenant_id": ident.tenant_id},
            )
        ).first()
        if row is None:
            # Tenant context is verified before this handler; missing metadata is a closed failure.
            from account_intelligence.api.problem import DependencyUnavailable

            raise DependencyUnavailable()
        plan = await limits.plan_code(s)
        plan_limits = await limits.limits(s)
        flag_rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT ON (flag) flag, rule FROM feature_flags"
                    " ORDER BY flag, version DESC"
                )
            )
        ).all()
        limits_now = await client_settings(s)
        used = await accounts_used(s, ident.user_id)
        total = int(
            await s.scalar(text("SELECT count(*) FROM accounts WHERE deleted_at IS NULL")) or 0
        )
        enabled_flags = {
            str(flag_row.flag): flags.evaluate(
                dict(flag_row.rule), str(flag_row.flag), ident.tenant_id, plan
            )
            for flag_row in flag_rows
        }

    role_table = roles.load_table()
    capabilities = sorted(
        CAPABILITIES[action]
        for action, role_set in role_table.actions.items()
        if action in CAPABILITIES and ident.role in role_set
    )
    plane = plane_of(request)
    mfa_enabled = "mfa" in ident.amr
    mfa_required = ident.role == "admin" and plane.policy.admin_requires_mfa
    return {
        "user_id": ident.user_id,
        "email": row.email,
        "display_name": row.display_name,
        "tenant_id": str(ident.tenant_id),
        "tenant_name": row.name,
        "role": ident.role,
        "plan": plan,
        "isolation_tier": row.isolation_tier,
        "capabilities": capabilities,
        "feature_flags": enabled_flags,
        "plan_limits": plan_limits,
        "mfa_enabled": mfa_enabled,
        "mfa_required": mfa_required,
        "mfa_step_up_fresh": sessions.step_up_fresh(
            ident.step_up_at, plane.clock.now(), plane.policy
        ),
        "accounts_used": used,
        "account_cap": limits_now.account_cap_per_user,
        "accounts_total": total,
        "mail_drive_allowed": limits_now.mail_drive_allowed,
        "web_refresh_allowed": limits_now.web_refresh_allowed,
        # the model is set up (a real or, in dev and test, the hermetic one); the client shows
        # one plain message when it is not, instead of failing quietly
        "model_ready": llm_state(getattr(request.app.state.container, "llm", None)) != LLM_MISSING,
    }
