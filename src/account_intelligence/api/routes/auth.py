"""First-party local password sign-in.

Credential lookups run through the narrow global identity functions. A tenant is selected only
after the password has been verified, and the server-side session is created in that tenant's
transaction after its live membership is checked again.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import ANONYMOUS, register, tenant_route
from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.platform.ports import NotConfigured
from identity import local_password, membership, rate_limit, sessions
from identity.local_password import PasswordRejected

public_router = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/auth", tags=["auth"])
register("POST", "/v1/auth/login", ANONYMOUS)
register("POST", "/v1/auth/email/resend", ANONYMOUS)
register("POST", "/v1/auth/email/verify", ANONYMOUS)
register("POST", "/v1/auth/password/reset/request", ANONYMOUS)
register("POST", "/v1/auth/password/reset", ANONYMOUS)
register("POST", "/v1/auth/password/enroll", tenant_route("manage_own_auth", "admin"))

_REFUSED = "Email or password is incorrect, or this account cannot sign in."
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")


class LoginBody(BaseModel):
    """Local login request. Tenant IDs are checked against verified membership."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    workspace_id: UUID | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Normalize and validate the login email address."""
        normalized = value.strip().lower()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class EmailBody(BaseModel):
    """Address used for an enumeration-resistant verification resend."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Normalize and validate the login email address."""
        normalized = value.strip().lower()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class VerifyEmailBody(BaseModel):
    """Single-use verification secret, submitted in the POST body rather than a URL."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=200)


class EnrollPasswordBody(BaseModel):
    """Password to link to the authenticated tenant user's verified address."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


class ResetPasswordBody(BaseModel):
    """Reset secret and replacement password."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


def _cookie(response: Response, token: str, request: Request) -> None:
    policy = plane_of(request).policy
    response.set_cookie(
        policy.session_cookie,
        token,
        max_age=policy.absolute_timeout_s,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(policy.preauth_cookie, path="/", secure=True, httponly=True)


@public_router.post(
    "/login",
    responses={200: {"model": C.SignedIn}, 409: {"model": C.TenantSelectionRequired}},
)
async def login(body: LoginBody, request: Request) -> Response:
    """Verify a password and establish one tenant-bound server-side session."""
    plane = plane_of(request)
    await _require_same_origin(request)
    await rate_limit.consume(request, account=body.email, action="login")
    now = plane.clock.now()
    async with untenanted_session() as session:
        credential = (
            await session.execute(
                text("SELECT * FROM local_auth_lookup(:email)"), {"email": body.email}
            )
        ).first()

    stored_hash = credential.password_hash if credential is not None else None
    valid_password = await local_password.verify_password(stored_hash, body.password)
    if (
        credential is None
        or not valid_password
        or credential.status != "active"
        or credential.email_verified_at is None
        or (credential.locked_until is not None and credential.locked_until > now)
    ):
        async with untenanted_session() as session:
            await session.execute(
                text("SELECT * FROM local_auth_failure(:email, :now)"),
                {"email": body.email, "now": now},
            )
        raise HTTPException(401, _REFUSED)

    async with untenanted_session() as session:
        memberships = (
            await session.execute(
                text("SELECT tenant_id, user_id, role FROM local_auth_memberships(:user_id)"),
                {"user_id": credential.user_id},
            )
        ).all()

    choices = {UUID(str(row.tenant_id)): (str(row.user_id), str(row.role)) for row in memberships}
    if len(choices) != len(memberships):
        # A duplicate verified email attached to two subjects in one tenant is ambiguous. Never
        # pick one nondeterministically; resolve the tenant's user records before allowing login.
        raise HTTPException(401, _REFUSED)
    if not choices:
        # Avoid distinguishing a verified identity with no current membership from bad credentials.
        raise HTTPException(401, _REFUSED)
    if body.workspace_id is None and len(choices) > 1:
        return JSONResponse(
            status_code=409,
            content={
                "code": "tenant_selection_required",
                "tenants": [
                    {"tenant_id": str(key), "role": membership_role}
                    for key, (_, membership_role) in choices.items()
                ],
            },
            headers={"Cache-Control": "no-store"},
        )
    tenant_id = body.workspace_id or next(iter(choices))
    if tenant_id not in choices:
        raise HTTPException(401, _REFUSED)

    user_id, role = choices[tenant_id]
    # No local TOTP verifier is wired yet. Admins cannot enter a session that the SSO path would
    # refuse under the same policy.
    if role == "admin" and plane.policy.admin_requires_mfa:
        raise HTTPException(401, _REFUSED)

    prior_token = request.cookies.get(plane.policy.session_cookie)
    prior_tenant: UUID | None = None
    if prior_token:
        async with untenanted_session() as session:
            found_tenant = await session.scalar(
                text("SELECT session_tenant(:token_hash)"),
                {"token_hash": sessions.token_hash(prior_token)},
            )
        if found_tenant is not None:
            prior_tenant = UUID(str(found_tenant))
    if prior_token and prior_tenant is not None and prior_tenant != tenant_id:
        async with tenant_txn(prior_tenant) as prior_session:
            prior = await sessions.load(prior_session, prior_token, now, plane.policy)
            if prior is not None:
                await sessions.revoke(prior_session, prior.id, "tenant_switch", now)

    async with tenant_txn(tenant_id) as session:
        rotated_from = None
        if prior_token and prior_tenant == tenant_id:
            prior = await sessions.load(session, prior_token, now, plane.policy)
            if prior is not None:
                await sessions.revoke(session, prior.id, "rotated", now)
                rotated_from = prior.id
        current_role = await membership.active_role(session, user_id)
        if current_role != role:
            raise HTTPException(401, _REFUSED)
        success = await session.scalar(
            text("SELECT local_auth_success(:user_id, :now)"),
            {"user_id": credential.user_id, "now": now},
        )
        if not success:
            raise HTTPException(401, _REFUSED)
        token, _session_id = await sessions.create(
            session,
            user_id=user_id,
            now=now.astimezone(UTC),
            policy=plane.policy,
            amr=("pwd",),
            rotated_from=rotated_from,
        )

    response = JSONResponse({"signed_in": True, "tenant_id": str(tenant_id)})
    _cookie(response, token, request)
    return response


def _verification_url(request: Request, token: str) -> str:
    """Put the raw verification secret in a URL fragment; browsers do not send fragments to
    servers or include them in HTTP access logs."""
    base = plane_of(request).base_url
    if not base or not _allowed_base_url(base):
        raise DependencyUnavailable()
    return f"{base.rstrip('/')}/verify-email#{token}"


def _password_reset_url(request: Request, token: str) -> str:
    """Keep reset secrets in the browser fragment, outside HTTP request URLs and access logs."""
    base = plane_of(request).base_url
    if not base or not _allowed_base_url(base):
        raise DependencyUnavailable()
    return f"{base.rstrip('/')}/reset#{token}"


async def _issue_verification(
    request: Request,
    *,
    session: AsyncSession,
    user_id: UUID,
    email: str,
    now: datetime,
    token: str,
) -> None:
    """Persist only a token hash, then deliver a fixed template through the configured mail port.

    The caller keeps the database transaction open until delivery reports success. If delivery is
    unavailable, the transaction rolls back so no user is left with an undelivered live token.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    issued = await session.scalar(
        text(
            "SELECT local_auth_issue_token(:user_id, 'email_verification', :hash, :expires, :now)"
        ),
        {
            "user_id": user_id,
            "hash": token_hash,
            "expires": now + timedelta(minutes=30),
            "now": now,
        },
    )
    if not issued:
        raise HTTPException(409, "verification could not be started")
    try:
        await plane_of(request).mailer.send(
            email,
            "local_email_verification",
            {"verification_url": _verification_url(request, token)},
        )
    except NotConfigured as error:
        raise DependencyUnavailable() from error


async def _require_same_origin(request: Request) -> None:
    """Refuse browser credential changes unless Origin matches the configured public app origin."""
    base = plane_of(request).base_url
    origin = request.headers.get("origin")
    if not base or not origin:
        raise HTTPException(403, "same-origin request required")
    try:
        from urllib.parse import urlsplit

        expected, supplied = urlsplit(base), urlsplit(origin)
    except ValueError:
        raise HTTPException(403, "same-origin request required") from None
    if (
        not _allowed_base_url(base)
        or supplied.scheme != expected.scheme
        or supplied.netloc != expected.netloc
        or supplied.path
        or supplied.query
        or supplied.fragment
    ):
        raise HTTPException(403, "same-origin request required")


def _allowed_base_url(base: str) -> bool:
    """Only public HTTPS origins are valid in cloud; dev/test may use loopback HTTP."""
    from urllib.parse import urlsplit

    parsed = urlsplit(base)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    from account_intelligence.config import get_settings

    return (
        get_settings().app_env in ("dev", "test")
        and parsed.scheme == "http"
        and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        and parsed.port in (None, 80, 3000, 3001, 3008)
    )


@router.post("/password/enroll", status_code=202, response_model=C.EmailVerificationSent)
async def enroll_password(
    body: EnrollPasswordBody,
    request: Request,
    ident: Identity = Depends(authorize),
) -> Response:
    """Start password enrollment for an already authenticated tenant member.

    The SSO session establishes which member is enrolling. The database email is never accepted
    from the request, and the new credential remains unusable until its email is verified.
    """
    now = plane_of(request).clock.now()
    await _require_same_origin(request)
    await rate_limit.consume(request, account=ident.user_id, action="enroll")
    try:
        async with tenant_txn(ident.tenant_id) as tenant_session:
            email = (
                await tenant_session.execute(
                    text(
                        "SELECT email FROM users WHERE tenant_id = :tenant_id"
                        " AND subject = :subject"
                    ),
                    {"tenant_id": ident.tenant_id, "subject": ident.user_id},
                )
            ).scalar_one_or_none()
            if not isinstance(email, str) or not _EMAIL.fullmatch(email):
                raise HTTPException(409, "an account email is required before password enrollment")
            email = email.lower()
            try:
                password_hash = await local_password.hash_password(body.password, email=email)
            except PasswordRejected as error:
                raise HTTPException(422, str(error)) from None
            token = secrets.token_urlsafe(32)
            async with untenanted_session() as auth_session:
                user_id = await auth_session.scalar(
                    text("SELECT local_auth_create(:email, :password_hash, :now)"),
                    {"email": email, "password_hash": password_hash, "now": now},
                )
                if user_id is None:
                    raise HTTPException(503, "password enrollment is unavailable")
                await _issue_verification(
                    request,
                    session=auth_session,
                    user_id=user_id,
                    email=email,
                    now=now,
                    token=token,
                )
            await audit_action(
                tenant_session,
                request,
                audit_class="admin",
                action="password_enroll",
                resource_type="user",
                resource_id=ident.user_id,
            )
    except IntegrityError:
        raise HTTPException(409, "a password credential already exists for this email") from None
    return JSONResponse({"verification_sent": True}, status_code=202)


@public_router.post("/email/resend", status_code=202, response_model=C.EmailVerificationSent)
async def resend_verification(body: EmailBody, request: Request) -> Response:
    """Resend a pending address verification with a generic response for unknown or active users."""
    await _require_same_origin(request)
    await rate_limit.consume(request, account=body.email, action="resend")
    token = secrets.token_urlsafe(32)
    now = plane_of(request).clock.now()
    try:
        async with untenanted_session() as auth_session:
            credential = (
                await auth_session.execute(
                    text("SELECT * FROM local_auth_lookup(:email)"), {"email": body.email}
                )
            ).first()
            if credential is not None and credential.status == "pending_verification":
                await _issue_verification(
                    request,
                    session=auth_session,
                    user_id=credential.user_id,
                    email=body.email,
                    now=now,
                    token=token,
                )
            else:
                # Keep unknown-account work on the Argon2 path and avoid a distinct immediate reply.
                await local_password.verify_password(None, secrets.token_urlsafe(24))
    except DependencyUnavailable:
        # No active token commits if mail/configuration failed. Keep the public response generic.
        pass
    return JSONResponse({"verification_sent": True}, status_code=202)


@public_router.post("/email/verify", response_model=C.EmailVerified)
async def verify_email(body: VerifyEmailBody, request: Request) -> Response:
    """Consume one live email-verification token; replay and expiry are generic refusals."""
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    await _require_same_origin(request)
    await rate_limit.consume(request, account=token_hash, action="verify")
    async with untenanted_session() as auth_session:
        identity_id = await auth_session.scalar(
            text("SELECT local_auth_verify_email(:hash, :now)"),
            {"hash": token_hash, "now": plane_of(request).clock.now()},
        )
    if identity_id is None:
        raise HTTPException(400, "verification link is invalid or expired")
    return JSONResponse({"verified": True})


@public_router.post(
    "/password/reset/request",
    status_code=202,
    response_model=C.PasswordResetAccepted,
)
async def request_password_reset(body: EmailBody, request: Request) -> Response:
    """Send a reset link when the address is eligible, with an enumeration-resistant response."""
    await _require_same_origin(request)
    await rate_limit.consume(request, account=body.email, action="password_reset")
    now = plane_of(request).clock.now()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        async with untenanted_session() as session:
            credential = (
                await session.execute(
                    text("SELECT * FROM local_auth_lookup(:email)"), {"email": body.email}
                )
            ).first()
            if (
                credential is not None
                and credential.status == "active"
                and credential.email_verified_at is not None
            ):
                issued = await session.scalar(
                    text(
                        "SELECT local_auth_issue_token(:user_id, 'password_reset', :hash,"
                        " :expires, :now)"
                    ),
                    {
                        "user_id": credential.user_id,
                        "hash": token_hash,
                        "expires": now + timedelta(minutes=20),
                        "now": now,
                    },
                )
                if issued:
                    try:
                        await plane_of(request).mailer.send(
                            body.email,
                            "local_password_reset",
                            {"reset_url": _password_reset_url(request, token)},
                        )
                    except NotConfigured as error:
                        raise DependencyUnavailable() from error
            else:
                await local_password.verify_password(None, secrets.token_urlsafe(24))
    except DependencyUnavailable:
        # Roll back token creation and avoid an existence oracle when delivery is unavailable.
        pass
    return JSONResponse({"accepted": True}, status_code=202)


@public_router.post(
    "/password/reset",
    response_model=C.PasswordResetCompleted,
)
async def reset_password(body: ResetPasswordBody, request: Request) -> Response:
    """Consume one reset token, replace the Argon2id hash and revoke active sessions."""
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    await _require_same_origin(request)
    await rate_limit.consume(request, account=token_hash, action="password_reset_verify")
    now = plane_of(request).clock.now()
    async with untenanted_session() as session:
        email = await session.scalar(
            text("SELECT local_auth_reset_email(:hash, :now)"),
            {"hash": token_hash, "now": now},
        )
    if not isinstance(email, str):
        await local_password.verify_password(None, body.password)
        raise HTTPException(400, "reset link is invalid or expired")
    try:
        password_hash = await local_password.hash_password(body.password, email=email)
    except PasswordRejected as error:
        raise HTTPException(422, str(error)) from None
    async with untenanted_session() as session:
        user_id = await session.scalar(
            text("SELECT local_auth_reset_password(:hash, :password_hash, :now)"),
            {"hash": token_hash, "password_hash": password_hash, "now": now},
        )
    if user_id is None:
        raise HTTPException(400, "reset link is invalid or expired")
    async with untenanted_session() as session:
        memberships = (
            await session.execute(
                text("SELECT tenant_id, user_id FROM local_auth_memberships(:user_id)"),
                {"user_id": user_id},
            )
        ).all()
    for member in memberships:
        async with tenant_txn(UUID(str(member.tenant_id))) as session:
            await sessions.revoke_all(session, str(member.user_id), "password_reset", now)
    return JSONResponse({"password_updated": True})  # nosec B105 -- a response flag, not a secret.
