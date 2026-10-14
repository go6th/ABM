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


async def latest(session: Any, user_id: str, limit: int = 20) -> list[Any]:
    """The user's latest notifications, unread first, with the account and job they are about."""
    return list(
        (
            await session.execute(
                text(
                    "SELECT n.id, n.kind, n.created_at, n.read_at, a.domain, coalesce(a.name,"
                    " a.domain) AS name,"
                    " j.scope, j.topic, j.credits_used, j.failure_reason"
                    " FROM notifications n LEFT JOIN accounts a ON a.id = n.account_id"
                    " LEFT JOIN research_jobs j ON j.id = n.job_id"
                    " WHERE n.user_id = :u ORDER BY (n.read_at IS NULL) DESC, n.created_at DESC"
                    " LIMIT :n"
                ),
                {"u": user_id, "n": limit},
            )
        ).all()
    )
