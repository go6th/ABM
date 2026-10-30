"""Open Door read side (OD1, OD2): signal events as ``signal_event.v1`` documents, a keyset cursor
and the page envelope. Every document is validated against the schema before it leaves; the
payload carries the graded fact reference, source codes and grade, never raw provider payloads,
provider names, credits or cost. No consumer, scheduler, sequencing or sending code exists here.

GUARANTEE(test_open_door_events_validate_and_breaking_changes_are_refused)
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from opendoor import events

PAGE = 50


def encode_cursor(at: datetime, event_id: UUID) -> str:
    """An opaque cursor (detected time, id)."""
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """The cursor back; ValueError when it is not one of ours."""
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    at, eid = raw.split("|", 1)
    return datetime.fromisoformat(at), UUID(eid)


def document(row: Any, codes: list[str]) -> dict[str, Any]:
    """One ``signal_event.v1`` document (validated)."""
    doc = {
        "schema": "signal_event.v1",
        "event_id": str(row.id),
        "account": row.domain,
        "signal_type": row.signal_type,
        "fingerprint": row.fingerprint,
        "grade": row.grade,
        "lead": bool(row.lead),
        "sensitive": bool(row.sensitive),
        "source_codes": codes,
        "score": int(row.score),
        "detected_at": row.detected_at.isoformat().replace("+00:00", "Z"),
        "fact_ref": str(row.fact_id),
    }
    events.validate("signal_event.v1.json", doc)
    return doc


async def page(s: Any, cursor: str | None, limit: int = PAGE) -> dict[str, Any]:
    """One page of the context tenant's signal events, oldest first."""
    after_at, after_id = decode_cursor(cursor) if cursor else (None, None)
    rows = (
        await s.execute(
            text(
                "SELECT e.id, a.domain, e.signal_type, e.fingerprint, e.grade, e.lead, e.sensitive,"
                " e.score, e.detected_at, e.fact_id FROM signal_events e JOIN accounts a"
                " ON a.id = e.account_id WHERE a.deleted_at IS NULL"
                " AND (CAST(:at AS timestamptz) IS NULL"
                " OR (e.detected_at, e.id) > (CAST(:at AS timestamptz), CAST(:id AS uuid)))"
                " ORDER BY e.detected_at, e.id LIMIT :n"
            ),
            {"at": after_at, "id": after_id, "n": limit + 1},
        )
    ).all()
    items = []
    for r in rows[:limit]:
        codes = [
            c
            for (c,) in (
                await s.execute(
                    text(
                        "SELECT DISTINCT source_code FROM signal_event_evidence WHERE event_id = :e"
                        " AND source_code IS NOT NULL ORDER BY source_code"
                    ),
                    {"e": r.id},
                )
            ).all()
        ]
        items.append(document(r, codes))
    nxt = (
        encode_cursor(rows[limit - 1].detected_at, rows[limit - 1].id)
        if len(rows) > limit
        else None
    )
    body = {"schema": "signal_page.v1", "items": items, "next_cursor": nxt}
    events.validate("signal_page.v1.json", body)
    return body
