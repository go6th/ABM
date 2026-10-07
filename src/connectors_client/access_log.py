"""Every connector access, logged (CN3): principal, connection, source, resource, purpose and
outcome in the insert-only ``connector_access_log`` (never a token or content). The one writer.

GUARANTEE(test_a_fetch_pass_is_bounded_resumable_logged_and_read_only)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text


async def log(
    s: Any,
    *,
    connection_id: UUID,
    principal: str,
    kind: str,
    purpose: str,
    outcome: str,
    at: datetime,
    resource_id: str | None = None,
) -> None:
    """One access row in the caller's transaction."""
    await s.execute(
        text(
            "INSERT INTO connector_access_log (connection_id, principal, kind, resource_id,"
            " purpose, outcome, at) VALUES (:c, :p, :k, :r, :pu, :o, :at)"
        ),
        {
            "c": connection_id,
            "p": principal,
            "k": kind,
            "r": None if resource_id is None else resource_id[:300],
            "pu": purpose,
            "o": outcome,
            "at": at,
        },
    )
