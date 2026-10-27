"""The entity lock on the account row (migration 0083): the one writer of
``accounts.entity_lock``, called by the research graph's record store. The latest research
wins; a build run reads its parent's lock back through ``load_entity_lock``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text


async def record_entity_lock(s: Any, *, run_id: UUID, lock: Mapping[str, Any]) -> None:
    """Store ``lock`` (``EntityLock.as_dict()``) on the run's account."""
    await s.execute(
        text(
            "UPDATE accounts SET entity_lock = CAST(:j AS jsonb), entity_lock_at = now()"
            " WHERE id = (SELECT account_id FROM account_runs WHERE id = :r)"
        ),
        {"j": json.dumps(dict(lock), sort_keys=True), "r": run_id},
    )


async def load_entity_lock(s: Any, *, run_id: UUID) -> dict[str, Any] | None:
    """The stored lock of the run's account, if any."""
    row = (
        await s.execute(
            text(
                "SELECT a.entity_lock FROM accounts a JOIN account_runs r ON r.account_id = a.id"
                " WHERE r.id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    return dict(row.entity_lock) if row is not None and row.entity_lock else None
