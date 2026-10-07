"""Broker keys (AD4, D-70): platform-held only in phase one. A key is a Secret Manager
REFERENCE, written once as a new version, never displayed, never logged, never returned (write
only). A run uses the version in force when it was created: a key change takes effect on new runs
only. Bring-your-own keys are behind a flag and refuse (NotConfigured) until D-70 is decided.

GUARANTEE(test_quotas_flags_and_keys_are_versioned_and_audited)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import text

from account_intelligence.platform.ports import NotConfigured

_REF = re.compile(r"^[A-Za-z0-9/_.:-]{1,255}$")


async def add_version(
    s: Any, *, provider_code: str, secret_ref: str, actor: str, reason_code: str, now: datetime
) -> int:
    """A new key reference version for a provider; returns the version (the ref is never read
    back by any route)."""
    if not re.fullmatch(r"p_[a-z]+", provider_code) or not _REF.match(secret_ref):
        raise ValueError("bad provider code or secret reference")
    version = int(
        (
            await s.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM broker_keys WHERE provider_code = :p"
                ),
                {"p": provider_code},
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO broker_keys (provider_code, version, secret_ref, created_by, reason_code,"
            " created_at) VALUES (:p, :v, :r, :a, :why, :now)"
        ),
        {
            "p": provider_code,
            "v": version,
            "r": secret_ref,
            "a": actor,
            "why": reason_code,
            "now": now,
        },
    )
    return version


async def effective_ref(s: Any, provider_code: str, run_created_at: datetime) -> str | None:
    """The key reference a run created at ``run_created_at`` uses (the newest version that
    existed then); None when no key was registered."""
    found: str | None = (
        await s.execute(
            text(
                "SELECT secret_ref FROM broker_keys WHERE provider_code = :p AND created_at <= :at"
                " ORDER BY version DESC LIMIT 1"
            ),
            {"p": provider_code, "at": run_created_at},
        )
    ).scalar_one_or_none()
    return found


async def versions(s: Any) -> list[dict[str, Any]]:
    """Every provider's versions WITHOUT the reference (write-only)."""
    rows = (
        await s.execute(
            text(
                "SELECT provider_code, version, created_by, created_at FROM broker_keys"
                " ORDER BY provider_code, version"
            )
        )
    ).all()
    return [
        {
            "provider_code": r.provider_code,
            "version": r.version,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def bring_your_own() -> None:
    """Tenant-supplied broker keys (D-70): refused until decided."""
    raise NotConfigured("bring-your-own broker keys are not enabled (decision D-70)")
