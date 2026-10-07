"""Server-side sessions (ID3).

The browser holds a 256-bit random token in an HttpOnly, Secure, SameSite cookie; the database
holds only its SHA-256. A session ends at its idle timeout, its absolute timeout, on revocation
(one session, or "sign out everywhere"), or when it is rotated (sign-in and privilege change
always issue a NEW token). Every end is a conditional update setting ``revoked_at`` (never a delete)
and an ``auth_events`` row. The token never appears in a URL, a log line, an audit row or an
error. Timeouts and the step-up window come from ``config/session.yaml`` (``status: proposal``).

GUARANTEE(test_sessions_expire_rotate_and_revoke)
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class SessionPolicy:
    """Timeouts, cookie names and windows (``config/session.yaml``)."""

    idle_timeout_s: int
    absolute_timeout_s: int
    touch_interval_s: int
    step_up_window_s: int
    admin_requires_mfa: bool
    session_cookie: str
    preauth_cookie: str
    csrf_header: str
    same_site: str
    state_ttl_s: int
    clock_skew_s: int
    max_assertion_bytes: int
    membership_cache_s: int
    download_ttl_s: int


@lru_cache
def load_policy() -> SessionPolicy:
    """The session policy from configuration (read once)."""
    raw = yaml.safe_load((CONFIG_DIR / "session.yaml").read_text())
    s, c, i = raw["sessions"], raw["cookies"], raw["sign_in"]
    policy = SessionPolicy(
        idle_timeout_s=int(s["idle_timeout_s"]),
        absolute_timeout_s=int(s["absolute_timeout_s"]),
        touch_interval_s=int(s["touch_interval_s"]),
        step_up_window_s=int(s["step_up_window_s"]),
        admin_requires_mfa=bool(s["admin_requires_mfa"]),
        session_cookie=str(c["session"]),
        preauth_cookie=str(c["preauth"]),
        csrf_header=str(c["csrf_header"]),
        same_site=str(c["same_site"]),
        state_ttl_s=int(i["state_ttl_s"]),
        clock_skew_s=int(i["clock_skew_s"]),
        max_assertion_bytes=int(i["max_assertion_bytes"]),
        membership_cache_s=int(raw["membership"]["cache_s"]),
        download_ttl_s=int(raw["downloads"]["token_ttl_s"]),
    )
    if not 0 < policy.idle_timeout_s <= policy.absolute_timeout_s:
        raise ValueError("idle timeout must be positive and at most the absolute timeout")
    if not policy.session_cookie.startswith("__Host-"):
        raise ValueError("the session cookie must use the __Host- prefix")
    return policy


def new_token() -> str:
    """A fresh 256-bit session token (URL-safe base64)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """The stored form of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ActiveSession:
    """A live session as the authorisation layer sees it."""

    id: UUID
    tenant_id: UUID
    user_id: str
    amr: tuple[str, ...]
    step_up_at: datetime | None
    created_at: datetime


async def event(
    s: AsyncSession,
    kind: str,
    now: datetime,
    *,
    user_id: str | None = None,
    session_id: UUID | None = None,
    connection_id: UUID | None = None,
    reason: str | None = None,
    request_id: str | None = None,
) -> None:
    """One ``auth_events`` row (identifiers and a fixed reason code only)."""
    await s.execute(
        text(
            "INSERT INTO auth_events (user_id, session_id, connection_id, event, reason_code,"
            " request_id, at) VALUES (:u, :s, :c, :e, :r, :q, :at)"
        ),
        {
            "u": user_id,
            "s": session_id,
            "c": connection_id,
            "e": kind,
            "r": reason,
            "q": request_id,
            "at": now,
        },
    )


async def create(
    s: AsyncSession,
    *,
    user_id: str,
    now: datetime,
    policy: SessionPolicy,
    connection_id: UUID | None = None,
    amr: tuple[str, ...] = (),
    step_up: bool = False,
    rotated_from: UUID | None = None,
) -> tuple[str, UUID]:
    """A new session in the context tenant; returns (token, session id). The token is returned
    once, to be set in the cookie, and never stored."""
    token = new_token()
    sid: UUID = (
        await s.execute(
            text(
                "INSERT INTO auth_sessions (user_id, token_hash, connection_id, amr, created_at,"
                " last_seen_at, absolute_expires_at, idle_timeout_s, step_up_at, rotated_from)"
                " VALUES (:u, :h, :c, :amr, :now, :now, :abs, :idle, :step, :rot) RETURNING id"
            ),
            {
                "u": user_id,
                "h": token_hash(token),
                "c": connection_id,
                "amr": list(amr),
                "now": now,
                "abs": now + timedelta(seconds=policy.absolute_timeout_s),
                "idle": policy.idle_timeout_s,
                "step": now if step_up else None,
                "rot": rotated_from,
            },
        )
    ).scalar_one()
    await event(
        s,
        "session_rotated" if rotated_from else "login_succeeded",
        now,
        user_id=user_id,
        session_id=sid,
        connection_id=connection_id,
    )
    if step_up:
        await event(s, "step_up", now, user_id=user_id, session_id=sid)
    return token, sid


async def revoke(s: AsyncSession, session_id: UUID, reason: str, now: datetime) -> bool:
    """End one session; True when this call ended it."""
    row = (
        await s.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = :now, revoke_reason = :r"
                " WHERE id = :i AND revoked_at IS NULL RETURNING user_id"
            ),
            {"now": now, "r": reason, "i": session_id},
        )
    ).first()
    if row is None:
        return False
    kind = (
        "session_expired"
        if reason in ("idle_timeout", "absolute_timeout")
        else ("session_rotated" if reason == "rotated" else "session_revoked")
    )
    if reason != "rotated":
        await event(s, kind, now, user_id=row.user_id, session_id=session_id, reason=reason)
    return True


async def revoke_all(s: AsyncSession, user_id: str, reason: str, now: datetime) -> int:
    """End every live session of one user ("sign out everywhere")."""
    rows = (
        await s.execute(
            text(
                "UPDATE auth_sessions SET revoked_at = :now, revoke_reason = :r"
                " WHERE user_id = :u AND revoked_at IS NULL RETURNING id"
            ),
            {"now": now, "r": reason, "u": user_id},
        )
    ).all()
    await event(s, "revoked_all", now, user_id=user_id, reason=reason)
    return len(rows)


async def load(
    s: AsyncSession, token: str, now: datetime, policy: SessionPolicy
) -> ActiveSession | None:
    """The live session for ``token`` in the context tenant, or None. An idle or absolute
    timeout ends the session here (and it can never be used again); activity is recorded at
    most once per ``touch_interval_s``."""
    row: Any = (
        await s.execute(
            text(
                "SELECT id, tenant_id, user_id, amr, created_at, last_seen_at,"
                " absolute_expires_at, idle_timeout_s, step_up_at, revoked_at"
                " FROM auth_sessions WHERE token_hash = :h"
            ),
            {"h": token_hash(token)},
        )
    ).first()
    if row is None or row.revoked_at is not None:
        return None
    if now >= row.absolute_expires_at:
        await revoke(s, row.id, "absolute_timeout", now)
        return None
    if now - row.last_seen_at >= timedelta(seconds=int(row.idle_timeout_s)):
        await revoke(s, row.id, "idle_timeout", now)
        return None
    if now - row.last_seen_at >= timedelta(seconds=policy.touch_interval_s):
        await s.execute(
            text(
                "UPDATE auth_sessions SET last_seen_at = :now"
                " WHERE id = :i AND revoked_at IS NULL AND last_seen_at < :now"
            ),
            {"now": now, "i": row.id},
        )
    return ActiveSession(
        row.id, row.tenant_id, row.user_id, tuple(row.amr or ()), row.step_up_at, row.created_at
    )


async def rotate(
    s: AsyncSession,
    current: ActiveSession,
    now: datetime,
    policy: SessionPolicy,
    *,
    step_up: bool = False,
    amr: tuple[str, ...] | None = None,
) -> tuple[str, UUID]:
    """End ``current`` and issue a new token for the same user (privilege change, step-up)."""
    await revoke(s, current.id, "rotated", now)
    return await create(
        s,
        user_id=current.user_id,
        now=now,
        policy=policy,
        amr=amr if amr is not None else current.amr,
        step_up=step_up,
        rotated_from=current.id,
    )


def step_up_fresh(step_up_at: datetime | None, now: datetime, policy: SessionPolicy) -> bool:
    """True when the last MFA sign-in is within the step-up window."""
    return step_up_at is not None and now - step_up_at < timedelta(seconds=policy.step_up_window_s)
