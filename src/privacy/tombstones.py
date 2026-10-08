"""Tombstones (D-83): proof of deletion with no content.

A tombstone records ids, type, time, reason and the hash of what was removed,
never the content itself. It reconciles retention and erasure with the
append-only rule: platform-held data is deleted after its sourced window or on
request; a client's own CRM or Drive records are never touched.

GUARANTEE(test_retention_removes_exactly_the_dry_run)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

Reason = str  # free text from config or the DSAR request id


@dataclass(frozen=True)
class Tombstone:
    """What is recorded when data is removed."""

    subject_type: str  # table or class, e.g. "raw_broker_payloads"
    subject_id: str
    reason: str
    content_sha256: str | None
    at: datetime


def content_hash(payload: Any) -> str:
    """The hash of what was removed, for audit; the content itself is not kept."""
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


async def write(
    s: Any,
    *,
    subject_type: str,
    subject_id: str,
    reason: str,
    content_sha256: str | None,
    tenant_id: UUID | None = None,
    at: datetime,
) -> None:
    """Record one tombstone (insert-only; idempotent per subject)."""
    await s.execute(
        text(
            "INSERT INTO tombstones (subject_type, subject_id, tenant_id, reason,"
            " content_sha256, at) VALUES (:t, :i, :tn, :r, :h, :at)"
            " ON CONFLICT (subject_type, subject_id) DO NOTHING"
        ),
        {
            "t": subject_type,
            "i": subject_id,
            "tn": tenant_id,
            "r": reason,
            "h": content_sha256,
            "at": at,
        },
    )
