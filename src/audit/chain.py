"""Audit chain verification (AU2): a bounded job that recomputes the context tenant's chain.

For every row in seq order: the seq must follow the previous one (a gap is a deleted row), the
row's ``prev_hash`` must be the previous row's ``hash`` (a broken link is a replaced or reordered
row), and the row's ``hash`` must equal the recomputed one (a mismatch is an edited row). The
chain's head must not be below the highest seq a previous check verified (a lower head is a
truncation). Each pass records one insert-only ``audit_chain_checks`` row.

GUARANTEE(test_audit_chain_detects_deleted_edited_and_truncated_rows)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit.events import GENESIS, AuditEvent, chain_hash


@dataclass(frozen=True)
class ChainCheck:
    """One verification pass."""

    ok: bool
    from_seq: int
    to_seq: int
    first_bad_seq: int | None
    reason: str | None
    exit_reason: str


def _event(row: Any) -> AuditEvent:
    return AuditEvent(
        at=row.at,
        actor=row.actor,
        actor_kind=row.actor_kind,
        audit_class=row.audit_class,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        route=row.route,
        request_id=row.request_id,
        outcome=row.outcome,
        support_grant_id=str(row.support_grant_id) if row.support_grant_id else None,
    )


def check_rows(rows: Sequence[Any], prev_seq: int, prev_hash: str) -> tuple[int | None, str | None]:
    """The first bad seq and why, over ``rows`` in seq order after (prev_seq, prev_hash). Pure."""
    for row in rows:
        seq = int(row.seq)
        if seq != prev_seq + 1:
            return prev_seq + 1, "seq_gap"
        if row.prev_hash != prev_hash:
            return seq, "broken_link"
        if row.hash != chain_hash(prev_hash, _event(row), seq):
            return seq, "hash_mismatch"
        prev_seq, prev_hash = seq, row.hash
    return None, None


async def verify(s: AsyncSession, now: datetime, *, batch: int, max_batches: int) -> ChainCheck:
    """One bounded pass over the whole chain of the context tenant."""
    prev_seq, prev_hash = 0, GENESIS
    bad: int | None = None
    reason: str | None = None
    exit_reason = "complete"
    # LOOP(audit chain verification): cap=max_batches batches of `batch` rows per pass;
    # exit=complete, first bad row found, batch_cap (the head is checked next pass)
    for _ in range(max_batches):
        rows = (
            await s.execute(
                text(
                    "SELECT seq, at, actor, actor_kind, audit_class, action, resource_type,"
                    " resource_id, route, request_id, outcome, support_grant_id, prev_hash, hash"
                    " FROM audit_events WHERE seq > :after ORDER BY seq LIMIT :n"
                ),
                {"after": prev_seq, "n": batch},
            )
        ).all()
        bad, reason = check_rows(rows, prev_seq, prev_hash)
        if bad is not None or len(rows) < batch:
            break
        prev_seq, prev_hash = int(rows[-1].seq), str(rows[-1].hash)
    else:
        exit_reason = "batch_cap"
    if bad is None:
        if rows:
            prev_seq = int(rows[-1].seq)
        verified: int = (
            await s.execute(
                text("SELECT coalesce(max(to_seq), 0) FROM audit_chain_checks WHERE ok")
            )
        ).scalar_one()
        if exit_reason == "complete" and prev_seq < int(verified):
            bad, reason = prev_seq + 1, "truncated"
    if bad is not None:
        exit_reason = reason or "bad"
    result = ChainCheck(bad is None, 0, prev_seq, bad, reason, exit_reason)
    await s.execute(
        text(
            "INSERT INTO audit_chain_checks (from_seq, to_seq, ok, first_bad_seq, reason, at)"
            " VALUES (:f, :t, :ok, :bad, :why, :at)"
        ),
        {"f": 0, "t": prev_seq, "ok": result.ok, "bad": bad, "why": reason, "at": now},
    )
    return result
