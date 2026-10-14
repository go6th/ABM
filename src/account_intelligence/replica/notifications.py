"""In-app notifications: the only writer of ``notifications``.

A notification names a user, a kind, and optionally the account and research job it is about.
The user reads them in the shell; marking one read only sets ``read_at`` once.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text


async def notify(
    session: Any,
    *,
    user_id: str,
    kind: str,
    account_id: UUID | None = None,
    job_id: UUID | None = None,
) -> None:
    """Tell one user something, in the caller's tenant transaction."""
    await session.execute(
        text(
            "INSERT INTO notifications (user_id, kind, account_id, job_id) VALUES (:u, :k, :a, :j)"
        ),
        {"u": user_id, "k": kind, "a": account_id, "j": job_id},
    )


async def mark_read(session: Any, notification_id: UUID, user_id: str) -> bool:
    """Mark one of the user's notifications read; False when it is not theirs or absent."""
    done = await session.scalar(
        text(
            "UPDATE notifications SET read_at = coalesce(read_at, now())"
            " WHERE id = :n AND user_id = :u RETURNING id"
        ),
        {"n": notification_id, "u": user_id},
    )
    return done is not None
