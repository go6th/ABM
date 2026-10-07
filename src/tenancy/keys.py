"""Cache and queue keys carry the tenant prefix (TN4, ARCH A5).

GUARANTEE(test_cache_keys_carry_the_tenant_prefix)
"""

from __future__ import annotations

from uuid import UUID

from tenancy.catalog import CACHE_PURPOSES


def cache_key(tenant_id: UUID, purpose: str, *parts: str) -> str:
    """``t:<tenant>:<purpose>:<parts..>`` for a registered purpose; refuses unknown purposes and
    parts containing the separator, so one tenant's key can never be spelled as another's."""
    if purpose not in CACHE_PURPOSES:
        raise ValueError(f"unregistered cache purpose {purpose!r}")
    for part in parts:
        if not part or ":" in part:
            raise ValueError("cache key parts must be non-empty and free of ':'")
    return ":".join(("t", str(tenant_id), purpose, *parts))


def tenant_of_cache_key(key: str) -> UUID | None:
    """The tenant a cache key belongs to, or None when it is not a tenant key."""
    head = key.split(":", 3)
    if len(head) < 3 or head[0] != "t":
        return None
    try:
        return UUID(head[1])
    except ValueError:
        return None
