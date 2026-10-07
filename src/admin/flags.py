"""Feature flags (AD4): insert-only versions, evaluated on the server by tenant, plan or
percentage, with an event per change. The percentage bucket is a stable hash of (flag, tenant),
so a tenant's answer does not change between requests. Pure evaluation; the store functions are
the one writer of ``feature_flags`` and ``flag_events``.

GUARANTEE(test_quotas_flags_and_keys_are_versioned_and_audited)
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text


def bucket(flag: str, tenant_id: UUID) -> int:
    """0 to 99, stable per (flag, tenant)."""
    return int(hashlib.sha256(f"{flag}:{tenant_id}".encode()).hexdigest()[:8], 16) % 100


def evaluate(rule: dict[str, Any] | None, flag: str, tenant_id: UUID, plan: str) -> bool:
    """On when the tenant is listed, its plan is listed, or its bucket is under the percentage;
    off for a missing rule (deny by default)."""
    if not rule:
        return False
    if str(tenant_id) in (rule.get("tenants") or ()):
        return True
    if plan in (rule.get("plans") or ()):
        return True
    percent = int(rule.get("percent") or 0)
    return bucket(flag, tenant_id) < max(0, min(100, percent))


def validate(rule: dict[str, Any]) -> dict[str, Any]:
    """A rule with only known keys and sane values (ValueError otherwise)."""
    if set(rule) - {"tenants", "plans", "percent"}:
        raise ValueError("unknown rule keys")
    tenants = [str(UUID(str(t))) for t in rule.get("tenants") or ()]
    plans = [str(p) for p in rule.get("plans") or ()]
    percent = int(rule.get("percent") or 0)
    if not 0 <= percent <= 100 or len(tenants) > 500 or len(plans) > 20:
        raise ValueError("rule out of range")
    return {"tenants": tenants, "plans": plans, "percent": percent}


async def current(s: Any, flag: str) -> dict[str, Any] | None:
    """The newest rule of a flag, or None."""
    row = (
        await s.execute(
            text("SELECT rule FROM feature_flags WHERE flag = :f ORDER BY version DESC LIMIT 1"),
            {"f": flag},
        )
    ).first()
    return None if row is None else dict(row.rule)


async def set_rule(
    s: Any, *, flag: str, rule: dict[str, Any], actor: str, reason_code: str, now: datetime
) -> int:
    """A new version of a flag and its change event; returns the version."""
    import json

    clean = validate(rule)
    version = int(
        (
            await s.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM feature_flags WHERE flag = :f"),
                {"f": flag},
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO feature_flags (flag, version, rule, created_by, created_at)"
            " VALUES (:f, :v, CAST(:r AS jsonb), :a, :now)"
        ),
        {"f": flag, "v": version, "r": json.dumps(clean), "a": actor, "now": now},
    )
    await s.execute(
        text(
            "INSERT INTO flag_events (flag, version, actor, reason_code, at)"
            " VALUES (:f, :v, :a, :r, :now)"
        ),
        {"f": flag, "v": version, "a": actor, "r": reason_code, "now": now},
    )
    return version
