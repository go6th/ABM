"""Seller usefulness events (D-68): identifiers only. ``first_call_question_used`` names a
first-call question section (marked or copied); ``confirmed_fact_referenced`` names a Confirmed
fact (opened, copied into a note, or included in an export a member downloaded). The subject must
exist in this tenant with the right kind or grade, so no event can name an invented subject. One
row per (kind, subject, user, via, day). The one writer of ``usefulness_events``.

GUARANTEE(test_usefulness_events_are_identifiers_only_and_counted_once)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

QUESTION, FACT = "first_call_question_used", "confirmed_fact_referenced"
VIA = {QUESTION: ("marked", "copied"), FACT: ("opened", "noted", "exported")}


class UnknownSubject(LookupError):
    """The subject is not a first-call question or a Confirmed fact of this tenant."""


async def _account_of(s: Any, kind: str, subject: UUID) -> UUID:
    if kind == QUESTION:
        sql = (
            "SELECT account_id FROM report_sections WHERE id = :i AND kind = 'first_call_question'"
        )
    else:
        sql = (
            "SELECT account_id FROM facts WHERE id = :i AND product_grade = 'confirmed'"
            " AND superseded_at IS NULL AND visibility = 'public'"
        )
    found: UUID | None = (await s.execute(text(sql), {"i": subject})).scalar_one_or_none()
    if found is None:
        raise UnknownSubject(kind)
    return found


async def record(
    s: Any, *, kind: str, subject: UUID, via: str, user_id: str, now: datetime
) -> bool:
    """One event; True when new (False for a repeat the same day)."""
    if via not in VIA.get(kind, ()):
        raise ValueError("unknown kind or channel")
    account = await _account_of(s, kind, subject)
    key = f"{kind}:{subject}:{user_id}:{via}:{now.date().isoformat()}"
    row = (
        await s.execute(
            text(
                "INSERT INTO usefulness_events (kind, subject_id, account_id, via, user_id,"
                " dedupe_key,"
                " at) VALUES (:k, :s, :a, :v, :u, :d, :now)"
                " ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING id"
            ),
            {"k": kind, "s": subject, "a": account, "v": via, "u": user_id, "d": key, "now": now},
        )
    ).first()
    return row is not None


async def exported(s: Any, *, run_id: UUID, user_id: str, now: datetime, limit: int) -> int:
    """``confirmed_fact_referenced`` (via ``exported``) for the Confirmed facts in a downloaded
    package of one run (the facts table rows of that run); returns how many were new."""
    rows = (
        await s.execute(
            text(
                "SELECT DISTINCT f.id, f.account_id FROM view_rows v JOIN facts f"
                " ON f.id::text = v.row_id WHERE v.run_id = :r AND v.table_kind = 'facts'"
                " AND f.product_grade = 'confirmed' AND f.superseded_at IS NULL"
                " AND f.visibility = 'public' ORDER BY f.id LIMIT :n"
            ),
            {"r": run_id, "n": limit},
        )
    ).all()
    new = 0
    for r in rows:
        key = f"{FACT}:{r.id}:{user_id}:exported:{now.date().isoformat()}"
        hit = (
            await s.execute(
                text(
                    "INSERT INTO usefulness_events (kind, subject_id, account_id, via, user_id,"
                    " dedupe_key, at) VALUES (:k, :s, :a, 'exported', :u, :d, :now)"
                    " ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING id"
                ),
                {"k": FACT, "s": r.id, "a": r.account_id, "u": user_id, "d": key, "now": now},
            )
        ).first()
        new += hit is not None
    return new
