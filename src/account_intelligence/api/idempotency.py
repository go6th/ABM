"""Canonical request fingerprints and idempotency replay checks.

Guarantees: equivalent JSON requests hash identically and changed requests never
reuse a stored result (GUARANTEE(test_idempotency_rejects_key_reuse_with_different_request)).
"""

from __future__ import annotations

import hashlib
import json
from typing import TypeVar

from account_intelligence.api.errors import APIProblem

ResponseT = TypeVar("ResponseT")


def canonical_request_hash(body: object) -> str:
    """Hash a JSON-compatible request body independent of object key order."""
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def replay_response(
    *,
    stored_hash: str | None,
    incoming_hash: str,
    stored_response: ResponseT,
) -> ResponseT:
    """Return the original response or reject unsafe key reuse."""
    if stored_hash is None or stored_hash != incoming_hash:
        raise APIProblem(
            409,
            "idempotency_key_reuse",
            "Idempotency key conflict",
            "The idempotency key was already used for a different request.",
        )
    return stored_response
