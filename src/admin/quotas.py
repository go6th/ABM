"""Quotas and plan assignments (AD4): insert-only versions. A quota version overrides one limit of
the tenant's plan (``metering.limits`` reads the newest); a plan assignment moves the tenant to
another plan. Both are recorded in the operator audit. The one writer of ``quota_versions`` and
``plan_assignments``.

GUARANTEE(test_quotas_flags_and_keys_are_versioned_and_audited)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from metering.limits import DIMENSIONS


class VersionConflict(RuntimeError):
    """Another change took this version first."""


async def set_quota(
    s: Any, *, dimension: str, limit_value: int, set_by: str, reason_code: str, now: datetime
) -> int:
    """A new quota version for the context tenant; returns the version."""
    if dimension not in DIMENSIONS or limit_value < 0:
        raise ValueError("unknown dimension or negative limit")
    version = int(
        (
            await s.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM quota_versions WHERE dimension = :d"
                ),
                {"d": dimension},
            )
        ).scalar_one()
    )
    try:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO quota_versions (dimension, version, limit_value, set_by,"
                    " reason_code,"
                    " created_at) VALUES (:d, :v, :l, :by, :r, :now)"
                ),
                {
                    "d": dimension,
                    "v": version,
                    "l": limit_value,
                    "by": set_by,
                    "r": reason_code,
                    "now": now,
                },
            )
    except IntegrityError:
        raise VersionConflict("quota changed concurrently") from None
    return version


async def assign_plan(
    s: Any, *, plan_code: str, assigned_by: str, reason_code: str, now: datetime
) -> int:
    """A new plan assignment version for the context tenant; returns the version."""
    known = (
        await s.execute(
            text("SELECT 1 FROM plan_limits WHERE plan_code = :p LIMIT 1"), {"p": plan_code}
        )
    ).first()
    if known is None:
        raise ValueError("unknown plan")
    version = int(
        (
            await s.execute(text("SELECT coalesce(max(version), 0) + 1 FROM plan_assignments"))
        ).scalar_one()
    )
    try:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO plan_assignments (version, plan_code, assigned_by, reason_code,"
                    " created_at) VALUES (:v, :p, :by, :r, :now)"
                ),
                {"v": version, "p": plan_code, "by": assigned_by, "r": reason_code, "now": now},
            )
    except IntegrityError:
        raise VersionConflict("plan changed concurrently") from None
    return version
