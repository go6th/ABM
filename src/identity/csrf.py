"""CSRF protection for cookie-authenticated, state-changing requests (ID3).

The token is an HMAC of the session id under a server key: the browser reads it from
``GET /v1/sso/session`` and sends it back in a header on every unsafe method; a cross-site page
can neither read it nor compute it. Pure (the key is passed in).

GUARANTEE(test_csrf_is_required_on_every_state_changing_cookie_call)
"""

from __future__ import annotations

import hashlib
import hmac

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def csrf_token(key: bytes, session_id: str) -> str:
    """The CSRF token of one session."""
    if len(key) < 32:
        raise ValueError("CSRF key must be at least 32 bytes")
    return hmac.new(key, f"csrf:{session_id}".encode(), hashlib.sha256).hexdigest()


def csrf_ok(key: bytes, session_id: str, presented: str | None) -> bool:
    """True when ``presented`` is this session's token (constant-time compare)."""
    if not presented:
        return False
    return hmac.compare_digest(csrf_token(key, session_id), presented)


def needs_csrf(method: str) -> bool:
    """True for every method that can change state."""
    return method.upper() not in SAFE_METHODS
