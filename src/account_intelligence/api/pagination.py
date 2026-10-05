"""Opaque, tenant-bound cursor primitives for stable keyset pagination.

The caller supplies its configured page limit and a secret from the secret adapter. These
helpers never query or choose ordering. GUARANTEE(test_signed_cursor_is_tenant_bound).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


class InvalidCursor(ValueError):
    """The cursor is malformed, altered, or belongs to another tenant or collection."""


def encode_cursor(*, tenant_id: str, collection: str, position: list[str], secret: bytes) -> str:
    """Encode the last stable keyset position into an authenticated opaque cursor."""
    payload = json.dumps(
        {"v": 1, "tenant": tenant_id, "collection": collection, "position": position},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + signature).rstrip(b"=")
    return token.decode("ascii")


def decode_cursor(cursor: str, *, tenant_id: str, collection: str, secret: bytes) -> list[str]:
    """Verify cursor integrity and scope before returning its keyset position."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.new(secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursor("invalid cursor")
        data: object = json.loads(payload)
        if not isinstance(data, dict):
            raise InvalidCursor("invalid cursor")
        if (
            data.get("v") != 1
            or data.get("tenant") != tenant_id
            or data.get("collection") != collection
            or not isinstance(data.get("position"), list)
            or not all(isinstance(value, str) for value in data["position"])
        ):
            raise InvalidCursor("invalid cursor")
        position = data["position"]
        return [value for value in position if isinstance(value, str)]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        if isinstance(error, InvalidCursor):
            raise
        raise InvalidCursor("invalid cursor") from None
