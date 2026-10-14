"""The admin console (``/operator/v1/console``; screens A1 to A4, A6, A7). 6th operators only.

* ``GET /overview?client=``: A1, the last 30 days: active clients and users, questions and the
  share answered from the workspace, credits used of the pooled total, questions per week (all
  clients or one), what people ask about (categories only), clients not using it, and the
  nightly isolation check.
* ``GET /clients`` and ``POST /clients``: A2, every client with users, accounts, this month's
  credits, last activity and status; Add client creates the client and its first user and
  returns an invite link to copy (nothing is sent).
* ``GET /clients/{tenant_id}``, ``PUT /clients/{tenant_id}`` and ``POST
  /clients/{tenant_id}/invite``: A3, limits per user, features, 6th Library publications, and a
  new invite link while the client's admin has not accepted.
* ``GET /users`` and ``POST /users/{tenant_id}/terminate`` or ``/re-enable``: A4, every user's
  counts and access; ending access keeps history and signs the user out everywhere.
* ``GET /runs``: A6, credits by purpose this month, the isolation check and the newest jobs.
* ``GET /defaults`` and ``PUT /defaults``: A7, the defaults new clients start from, the credit
  cap per pull, the model split, the research skill version and the admins.

Each client is read inside its own transaction (row level security still applies); usage is
counts and categories only, never question text, answer text or file contents. Every change is
an ``operator_audit`` row and, for a client, an entry in that client's own audit log.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.operator import StaffIdentity, require_staff
from account_intelligence.api.policy import OperatorRole, operator_route, register
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.replica import admin as A
from account_intelligence.replica import isolation
from account_intelligence.replica import library as L
from account_intelligence.replica import settings as S
from admin import tenants
from audit.events import AuditEvent, record
from identity import local_password, membership, sessions
from tenancy import lifecycle

router = APIRouter(prefix="/operator/v1/console", tags=["admin-console"])
OPS: frozenset[OperatorRole] = frozenset({"operator"})
_BASE = "/operator/v1/console"
for _m, _p, _audit, _tenant in (
    ("GET", "/overview", "read", "none"),
    ("GET", "/clients", "read", "none"),
    ("POST", "/clients", "admin", "none"),
    ("GET", "/clients/{tenant_id}", "read", "operator_target"),
    ("PUT", "/clients/{tenant_id}", "admin", "operator_target"),
    ("POST", "/clients/{tenant_id}/invite", "admin", "operator_target"),
    ("GET", "/users", "read", "none"),
    ("POST", "/users/{tenant_id}/terminate", "admin", "operator_target"),
    ("POST", "/users/{tenant_id}/re-enable", "admin", "operator_target"),
    ("GET", "/runs", "read", "none"),
    ("GET", "/defaults", "read", "none"),
    ("PUT", "/defaults", "admin", "none"),
):
    register(_m, _BASE + _p, operator_route(_audit, tenant=_tenant, roles=OPS))  # type: ignore[arg-type]

INVITE_HOURS = 24  # the database refuses longer invite tokens
# The invited first user signs in with a password, and an admin session needs multi-factor
# sign-in that the password path cannot give yet (config/session.yaml admin_requires_mfa), so
# they join as a member; client admin rights come through the client's own SSO with MFA.
INVITED_ROLE = "analyst"
RUNS_SHOWN = 100
SKILL_DIR = Path("skills/abm-account-research")
Idle = Literal[7, 10, 14, 30]
Schedule = Literal["3d", "weekly", "2w", "monthly"]


async def _client_ids() -> list[UUID]:
    """Every real client (the isolation check's own pair is left out)."""
    async with untenanted_session() as plain:
        rows = await plain.execute(
            text("SELECT tenant_id FROM operator_tenant_overview() ORDER BY created_at")
        )
        skip = await isolation.check_clients(plain)
        return [UUID(str(r.tenant_id)) for r in rows if UUID(str(r.tenant_id)) not in skip]


async def _isolation() -> dict[str, Any]:
    async with untenanted_session() as plain:
        return await isolation.latest(plain)


async def _known(tenant_id: UUID) -> None:
    if tenant_id not in set(await _client_ids()):
        raise APIProblem(404, "not_found", "Not found", "No such client.")


async def _op_audit(request: Request, who: StaffIdentity, action: str, target: str) -> None:
    async with untenanted_session() as plain:
        await tenants.audit(
            plain,
            operator=who.staff_id,
            action=action,
            target=target,
            reason_code=None,
            request_id=getattr(request.state, "request_id", None),
            now=plane_of(request).clock.now(),
        )


async def _client_audit(s: Any, request: Request, who: StaffIdentity, action: str) -> None:
    """The client's own audit entry for an operator change (customer-visible)."""
    await record(
        s,
        AuditEvent(
            at=plane_of(request).clock.now(),
            actor=who.staff_id,
            actor_kind="operator",
            audit_class="admin",
            action=f"operator.{action}",
            resource_type="tenant",
            resource_id=None,
            route=None,
            request_id=getattr(request.state, "request_id", None),
        ),
    )


@router.get("/overview", response_model=C.ConsoleOverview)
async def overview(
    request: Request,
    client: UUID | None = Query(default=None),
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """A1: the last 30 days across clients (or the weekly chart for one client)."""
    now = plane_of(request).clock.now()
    rows: list[dict[str, Any]] = []
    weekly: dict[str, int] = {}
    cats: dict[str, int] = {}
    for tid in await _client_ids():
        async with tenant_txn(tid) as s:
            row = await A.client_row(s, tid, now)
            rows.append(row)
            if client is None or client == tid:
                for week, n in (await A.weekly(s, now)).items():
                    weekly[week] = weekly.get(week, 0) + n
            for key, n in (await A.categories(s, now)).items():
                cats[key] = cats.get(key, 0) + n
    if client is not None and str(client) not in {r["tenant_id"] for r in rows}:
        raise APIProblem(404, "not_found", "Not found", "No such client.")
    if not weekly:
        weekly = dict.fromkeys(A.weekly_keys(now), 0)
    idle = [r for r in rows if r["status"] in ("Idle", "Expiring")]
    idle.sort(key=lambda r: (r["expires_in_days"] is None, r["expires_in_days"] or 0))
    return {
        "clients": len(rows),
        "active_clients": sum(1 for r in rows if r["active_users"] > 0),
        "users": sum(r["users"] for r in rows),
        "active_users": sum(r["active_users"] for r in rows),
        "questions": sum(r["questions"] for r in rows),
        "answered_locally": sum(r["answered_locally"] for r in rows),
        "credits_used": sum(r["credits_used"] for r in rows),
        "credit_pool": sum(r["credit_pool"] for r in rows),
        "weekly": [{"week": w, "questions": n} for w, n in sorted(weekly.items())],
        "weekly_client": str(client) if client else None,
        "categories": [
            {"key": k, "label": A.CATEGORY_LABEL.get(k, k), "questions": n}
            for k, n in sorted(cats.items(), key=lambda kv: -kv[1])
            if n
        ],
        "not_using": idle,
        "client_names": [{"tenant_id": r["tenant_id"], "name": r["name"]} for r in rows],
        "isolation": await _isolation(),
    }


@router.get("/clients", response_model=C.ConsoleClients)
async def clients(request: Request, who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """A2: every client."""
    now = plane_of(request).clock.now()
    out = []
    for tid in await _client_ids():
        async with tenant_txn(tid) as s:
            out.append(await A.client_row(s, tid, now))
    return {"clients": out}


class NewClient(BaseModel):
    """Add client: its name and its first user's email (the design calls it the admin email)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    admin_email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _invite(request: Request, email: str) -> tuple[UUID, str, str]:
    """The local identity for ``email`` (created when new) and a fresh invite link for it. The
    link's secret sits in the fragment, outside request URLs and logs."""
    now = plane_of(request).clock.now()
    base = plane_of(request).base_url
    if not base:
        raise APIProblem(503, "not_configured", "Not configured", "No public base URL is set.")
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=INVITE_HOURS)
    async with untenanted_session() as plain:
        found = (
            await plain.execute(text("SELECT * FROM local_auth_lookup(:e)"), {"e": email})
        ).first()
        if found is None:
            unusable = await local_password.hash_password(secrets.token_urlsafe(32), email=email)
            identity_id = await plain.scalar(
                text("SELECT local_auth_create(:e, :h, :now)"),
                {"e": email, "h": unusable, "now": now},
            )
        else:
            identity_id = found.user_id
        issued = await plain.scalar(
            text("SELECT local_auth_issue_token(:u, 'invite_acceptance', :h, :x, :now)"),
            {
                "u": identity_id,
                "h": hashlib.sha256(token.encode()).hexdigest(),
                "x": expires,
                "now": now,
            },
        )
    if not issued:
        raise APIProblem(409, "invite_refused", "Invite refused", "This address cannot be invited.")
    return UUID(str(identity_id)), f"{base.rstrip('/')}/invite#{token}", expires.isoformat()


@router.post("/clients", response_model=C.ClientInvite, status_code=201)
async def add_client(
    body: NewClient, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A2 Add client: the client, its settings (invited) and its first user; an invite link to
    copy is returned and nothing is sent."""
    now = plane_of(request).clock.now()
    email = body.admin_email.strip().lower()
    identity_id, url, expires = await _invite(request, email)
    async with untenanted_session() as plain:
        tid = await tenants.create(plain, name=body.name.strip())
    async with tenant_txn(tid) as s:
        await lifecycle.append(
            s, await lifecycle.state(s), "created", actor=who.staff_id, plane="operator", now=now
        )
        await S.start_invited(s, who.staff_id)
        await membership.add(
            s,
            user_id=f"local:{identity_id}",
            role=INVITED_ROLE,
            actor=who.staff_id,
            now=now,
            email=email,
        )
        await _client_audit(s, request, who, "client.create")
    await _op_audit(request, who, "client.create", str(tid))
    return {"tenant_id": str(tid), "email": email, "invite_url": url, "expires_at": expires}


async def _pending(s: Any) -> list[str]:
    """Emails of this client's users whose invite has not been accepted yet."""
    emails = [
        str(r.email)
        for r in await s.execute(
            text(
                "SELECT u.email FROM memberships m JOIN users u ON u.subject = m.user_id"
                " WHERE m.status = 'active' AND u.email IS NOT NULL AND m.user_id LIKE 'local:%'"
                " ORDER BY u.email"
            )
        )
    ]
    out = []
    async with untenanted_session() as plain:
        for email in emails:
            found = (
                await plain.execute(text("SELECT status FROM local_auth_lookup(:e)"), {"e": email})
            ).first()
            if found is not None and found.status == "pending_verification":
                out.append(email)
    return out


@router.post("/clients/{tenant_id}/invite", response_model=C.ClientInvite)
async def renew_invite(
    tenant_id: UUID, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A3: a new invite link for the invited user who has not accepted yet (the old one stops
    working)."""
    await _known(tenant_id)
    async with tenant_txn(tenant_id) as s:
        pending = await _pending(s)
    if not pending:
        raise APIProblem(409, "no_pending_invite", "Nothing to renew", "Everyone invited joined.")
    _, url, expires = await _invite(request, pending[0])
    await _op_audit(request, who, "client.invite", str(tenant_id))
    return {
        "tenant_id": str(tenant_id),
        "email": pending[0],
        "invite_url": url,
        "expires_at": expires,
    }


async def _detail(tenant_id: UUID, now: Any) -> dict[str, Any]:
    async with untenanted_session() as plain:
        library = [a for a in await L.library_list(plain) if a["check_passed"]]
    async with tenant_txn(tenant_id) as s:
        row = await A.client_row(s, tenant_id, now)
        cs = await S.client_settings(s)
        here = await L.published_here(s)
        idle = [
            A.days_between(r.last_active_at, now)
            for r in await s.execute(
                text(
                    "SELECT last_active_at FROM memberships WHERE status = 'active'"
                    " AND access_state = 'active' AND last_active_at IS NOT NULL"
                )
            )
        ]
        pending = await _pending(s)
    return {
        "client": row,
        "settings": {f: getattr(cs, f) for f in S.CLIENT_FIELDS},
        "library": [
            {
                "library_account_id": a["id"],
                "name": a["name"],
                "domain": a["domain"],
                "published": a["id"] in here,
            }
            for a in library
        ],
        "user_idle_days": idle,
        "pending_invites": pending,
        "isolation": await _isolation(),
    }


@router.get("/clients/{tenant_id}", response_model=C.ConsoleClientDetail)
async def client_detail(
    tenant_id: UUID, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A3: one client's limits, features and library publications."""
    await _known(tenant_id)
    return await _detail(tenant_id, plane_of(request).clock.now())


class ClientChange(BaseModel):
    """A3 Save changes (only the fields sent change)."""

    model_config = ConfigDict(extra="forbid")
    account_cap_per_user: int | None = Field(default=None, ge=1, le=10_000)
    idle_expiry_days: Idle | None = None
    monthly_credit_pool: int | None = Field(default=None, ge=0, le=1_000_000)
    fastest_schedule: Schedule | None = None
    mail_drive_allowed: bool | None = None
    web_refresh_allowed: bool | None = None
    library_account_ids: list[UUID] | None = Field(default=None, max_length=500)


@router.put("/clients/{tenant_id}", response_model=C.ConsoleClientDetail)
async def change_client(
    tenant_id: UUID,
    body: ClientChange,
    request: Request,
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """A3 Save changes: limits, features, and which passed library accounts it has."""
    await _known(tenant_id)
    now = plane_of(request).clock.now()
    wanted: set[str] | None = None
    if body.library_account_ids is not None:
        wanted = {str(i) for i in body.library_account_ids}
        async with untenanted_session() as plain:
            passed = {a["id"] for a in await L.library_list(plain) if a["check_passed"]}
        if wanted - passed:
            raise APIProblem(
                409,
                "check_failed",
                "Not ready for clients",
                "Only an account whose client-ready check passed can be published.",
            )
    values = body.model_dump(exclude_none=True, exclude={"library_account_ids"})
    async with tenant_txn(tenant_id) as s:
        await S.update_client(s, values, who.staff_id, now)
        if wanted is not None:
            here = await L.published_here(s)
            for lib in sorted(wanted - here):
                await L.publish(s, UUID(lib), who.staff_id)
            for lib in sorted(here - wanted):
                await L.unpublish(s, UUID(lib))
        await _client_audit(s, request, who, "client.settings")
    await _op_audit(request, who, "client.settings", str(tenant_id))
    return await _detail(tenant_id, now)


@router.get("/users", response_model=C.ConsoleUsers)
async def users(request: Request, who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """A4: every user's counts and access, across clients."""
    now = plane_of(request).clock.now()
    out: list[dict[str, Any]] = []
    for tid in await _client_ids():
        async with tenant_txn(tid) as s:
            name = str(await s.scalar(text("SELECT name FROM tenants WHERE id = :t"), {"t": tid}))
            out.extend({**u, "tenant_id": str(tid), "client": name} for u in await A.users(s, now))
    return {"users": out}


class AccessTarget(BaseModel):
    """The user whose access changes."""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=300)


async def _access(
    tenant_id: UUID, body: AccessTarget, request: Request, who: StaffIdentity, end: bool
) -> dict[str, Any]:
    await _known(tenant_id)
    now = plane_of(request).clock.now()
    async with tenant_txn(tenant_id) as s:
        if await membership.get(s, body.user_id) is None:
            raise APIProblem(404, "not_found", "Not found", "No such user in this client.")
        if end:
            changed = await membership.end_access(s, body.user_id, "terminated", who.staff_id, now)
            if changed:
                await sessions.revoke_all(s, body.user_id, "access_terminated", now)
        else:
            changed = await membership.reenable(s, body.user_id, who.staff_id, now)
        state = await s.scalar(
            text("SELECT access_state FROM memberships WHERE user_id = :u"), {"u": body.user_id}
        )
        await _client_audit(s, request, who, "user.terminate" if end else "user.re_enable")
    await _op_audit(request, who, "user.terminate" if end else "user.re_enable", str(tenant_id))
    cache = getattr(request.app.state, "membership_cache", None)
    if cache is not None:
        cache.forget(tenant_id, body.user_id)
    return {"changed": changed, "access": str(state)}


@router.post("/users/{tenant_id}/terminate", response_model=C.AccessChanged)
async def terminate(
    tenant_id: UUID,
    body: AccessTarget,
    request: Request,
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """A4 Terminate: end the user's access now (history kept, signed out everywhere)."""
    return await _access(tenant_id, body, request, who, True)


@router.post("/users/{tenant_id}/re-enable", response_model=C.AccessChanged)
async def re_enable(
    tenant_id: UUID,
    body: AccessTarget,
    request: Request,
    who: StaffIdentity = Depends(require_staff),
) -> dict[str, Any]:
    """A4 Re-enable: restore access ended by an admin or by idle expiry."""
    return await _access(tenant_id, body, request, who, False)


@router.get("/runs", response_model=C.ConsoleRuns)
async def runs(request: Request, who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """A6: credits by purpose this month, the isolation check and the newest jobs."""
    now = plane_of(request).clock.now()
    purpose: dict[str, int] = {}
    jobs: list[dict[str, Any]] = []
    ids = await _client_ids()
    for tid in ids:
        async with tenant_txn(tid) as s:
            name = str(await s.scalar(text("SELECT name FROM tenants WHERE id = :t"), {"t": tid}))
            for key, n in (await A.credits_by_purpose(s, now)).items():
                purpose[key] = purpose.get(key, 0) + n
            jobs.extend(
                {**j, "tenant_id": str(tid), "client": name} for j in await A.runs(s, RUNS_SHOWN)
            )
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {
        "by_purpose": [
            {"key": k, "label": A.PURPOSE_LABEL[k], "credits": purpose.get(k, 0)}
            for k in A.PURPOSE_LABEL
        ],
        "isolation": await _isolation(),
        "workspaces": len(ids),
        "runs": jobs[:RUNS_SHOWN],
    }


def _admins() -> list[dict[str, str]]:
    def emails(name: str) -> list[str]:
        return [e.strip().lower() for e in os.environ.get(name, "").split(",") if e.strip()]

    out = [{"email": e, "role": "Operator"} for e in emails("STAFF_OPERATOR_EMAILS")]
    out += [{"email": e, "role": "Support"} for e in emails("STAFF_SUPPORT_EMAILS")]
    return out


def _skill_version() -> str:
    doc = yaml.safe_load(Path("config/skill_versions.yaml").read_text(encoding="utf-8"))
    return str(doc["current"]["value"])


async def _defaults(request: Request) -> dict[str, Any]:
    async with untenanted_session() as plain:
        d = await S.defaults(plain)
    return {
        "account_cap_per_user": int(d["account_cap_per_user"]),
        "idle_expiry_days": int(d["idle_expiry_days"]),
        "monthly_credit_pool": int(d["monthly_credit_pool"]),
        "fastest_schedule": str(d["fastest_schedule"]),
        "credit_cap_per_pull": int(d["credit_cap_per_pull"]),
        "model_split": str(d["model_split"]),
        "model_router_on": getattr(request.app.state.container, "router", None) is not None,
        "skill_version": _skill_version(),
        "skill_installed": SKILL_DIR.is_dir(),
        "admins": _admins(),
        "updated_by": str(d["updated_by"]),
        "updated_at": d["updated_at"].isoformat(),
    }


@router.get("/defaults", response_model=C.ConsoleDefaults)
async def defaults(request: Request, who: StaffIdentity = Depends(require_staff)) -> dict[str, Any]:
    """A7: the platform defaults."""
    return await _defaults(request)


class DefaultsChange(BaseModel):
    """A7 Save defaults (only the fields sent change; new clients start from them)."""

    model_config = ConfigDict(extra="forbid")
    account_cap_per_user: int | None = Field(default=None, ge=1, le=10_000)
    idle_expiry_days: Idle | None = None
    monthly_credit_pool: int | None = Field(default=None, ge=0, le=1_000_000)
    fastest_schedule: Literal["3d", "weekly", "2w"] | None = None
    credit_cap_per_pull: int | None = Field(default=None, ge=1, le=100_000)
    model_split: Literal["split", "single"] | None = None


@router.put("/defaults", response_model=C.ConsoleDefaults)
async def change_defaults(
    body: DefaultsChange, request: Request, who: StaffIdentity = Depends(require_staff)
) -> dict[str, Any]:
    """A7 Save defaults."""
    async with untenanted_session() as plain:
        await S.update_defaults(
            plain, body.model_dump(exclude_none=True), who.staff_id, plane_of(request).clock.now()
        )
    await _op_audit(request, who, "platform.defaults", "platform")
    return await _defaults(request)
