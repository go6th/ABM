"""Opaque staff sessions (fix prompt B, task 3; ADR-0111).

The operator console trades a verified staff IdP token (with a fresh MFA step-up) once for a
session token: ``ops_`` + 256 random bits, url-safe. Only its SHA-256 is stored, in
``operator_sessions`` (staff plane, no tenant). The token is then the console's bearer for every
staff route; ``api.operator.staff`` resolves it here.

* Idle: a session unused for ``idle_timeout_s`` (30 minutes) ends.
* Absolute: a session ends ``absolute_timeout_s`` (8 hours) after sign-in, however active. A
  session token can never mint another session (``mint`` takes an IdP identity only), so the
  limit cannot be stretched.
* Sign-out revokes it at once. Rows stay for audit.
* The staff member's MFA time (``step_up_at``) is the IdP sign-in's: every staff route's own
  step-up rule (``config/session.yaml`` ``step_up_window_s``) still applies on top.

The timeouts are the customer session's (``identity.sessions.load_policy``).

GUARANTEE(test_a_session_ends_after_30_minutes_idle)
GUARANTEE(test_a_session_ends_8_hours_after_sign_in_however_active)
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from account_intelligence.api.operator import StaffIdentity
from identity.sessions import SessionPolicy

PREFIX = "ops_"
_RANDOM_BYTES = 32  # 256 bits


@dataclass(frozen=True)
class Minted:
    """A new session: the token (shown once) and when it ends at the latest."""

    token: str
    expires_at: datetime


def is_session_token(token: str) -> bool:
    """Whether a bearer token is one of ours (never an IdP token)."""
    return token.startswith(PREFIX)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


async def mint(s: Any, who: StaffIdentity, now: datetime, policy: SessionPolicy) -> Minted:
    """Start a session for a staff member verified by the IdP."""
    token = PREFIX + secrets.token_urlsafe(_RANDOM_BYTES)
    expires = now + timedelta(seconds=policy.absolute_timeout_s)
    await s.execute(
        text(
            "INSERT INTO operator_sessions (token_hash, staff_id, role, step_up_at, created_at,"
            " last_seen_at, expires_at) VALUES (:h, :s, :r, :u, :now, :now, :e)"
        ),
        {
            "h": _digest(token),
            "s": who.staff_id,
            "r": who.role,
            "u": who.step_up_at,
            "now": now,
            "e": expires,
        },
    )
    return Minted(token, expires)


async def resolve(s: Any, token: str, now: datetime, policy: SessionPolicy) -> StaffIdentity | None:
    """The staff member of a live session (None: unknown, revoked, idle or past its end). A
    live session's last use moves to ``now``."""
    row = (
        await s.execute(
            text(
                "SELECT id, staff_id, role, step_up_at, last_seen_at, expires_at FROM"
                " operator_sessions WHERE token_hash = :h AND revoked_at IS NULL"
            ),
            {"h": _digest(token)},
        )
    ).first()
    if row is None or now >= row.expires_at:
        return None
    if now - row.last_seen_at >= timedelta(seconds=policy.idle_timeout_s):
        return None
    await s.execute(
        text(
            "UPDATE operator_sessions SET last_seen_at = :now WHERE id = :i AND last_seen_at < :now"
        ),
        {"now": now, "i": row.id},
    )
    return StaffIdentity(row.staff_id, row.role, row.step_up_at)


async def revoke(s: Any, token: str, now: datetime) -> bool:
    """End a session; False when there was no live one."""
    done = (
        await s.execute(
            text(
                "UPDATE operator_sessions SET revoked_at = :now"
                " WHERE token_hash = :h AND revoked_at IS NULL RETURNING id"
            ),
            {"now": now, "h": _digest(token)},
        )
    ).first()
    return done is not None
