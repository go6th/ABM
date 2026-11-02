"""What was and was not read of each uploaded file (Ask redesign 3a, 2026-11-02).

A zip is read member by member and every member's text is stored, but a member can be skipped
(an unsupported type, a corrupt or oversized member, the member limit), read without yielding any
text (a scanned PDF, an empty file), or a whole file can still be processing or have failed. The
file list on the web says so by name (``documents.service``); Ask did not, so the model answered
as if it had seen every page. This module reads the same record — ``document_parts.evidence``
(``members_read``, ``skipped``), the document's status and its failed parts — plus the number of
members that stored at least one passage, and returns one plain entry per file. The file map
(``documents.filemap``) prints it at the head of every Ask turn.

No model call, no file text: names, counts and reasons only (a member's name is the caller's own).

GUARANTEE(test_coverage_reports_skipped_members_unread_members_and_unfinished_files)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

from documents import repo

MEMBER = "coalesce(p.location->>'member', p.location->'from'->>'member')"
LIMIT = 200


async def coverage(session: Any, accounts: Sequence[UUID]) -> list[dict[str, Any]]:
    """One entry per uploaded file of these accounts, newest first: its name, status, and for an
    archive the members read, skipped (name and reason), and read without any text."""
    if not accounts:
        return []
    from documents.service import MESSAGES, SKIP_CHECKS

    docs = (
        await session.execute(
            text(
                "SELECT d.id, d.name, d.status, d.detected_type, d.reject_reason, d.stop_detail"
                " FROM documents d WHERE d.account_id = ANY(:a) AND EXISTS (SELECT 1 FROM"
                " accounts x WHERE x.id = d.account_id AND x.deleted_at IS NULL)"
                " ORDER BY d.created_at DESC, d.id LIMIT :n"
            ),
            {"a": list(accounts), "n": LIMIT},
        )
    ).all()
    counts = await repo.part_counts(session, [d.id for d in docs])
    archives = [d.id for d in docs if counts.get(d.id, {}).get("members_read")]
    with_text: dict[UUID, int] = {}
    if archives:
        found = await session.execute(
            text(
                f"SELECT p.document_id, count(DISTINCT {MEMBER}) AS n FROM document_passages p"  # nosec B608 -- fixed expression.
                f" WHERE p.document_id = ANY(:ids) AND {MEMBER} IS NOT NULL GROUP BY 1"
            ),
            {"ids": archives},
        )
        with_text = {r.document_id: int(r.n) for r in found}
    out = []
    for d in docs:
        c = counts.get(d.id, {})
        read = int(c.get("members_read", 0))
        entry: dict[str, Any] = {
            "document": str(d.name or "an uploaded file"),
            "status": str(d.status),
            "type": str(d.detected_type or ""),
        }
        if d.status in ("rejected", "failed"):
            code = str(d.reject_reason or d.stop_detail or "")
            entry["why"] = MESSAGES["empty"] if code == "parse:empty" else MESSAGES.get(code, "")
        if read or c.get("skipped"):
            entry["members_read"] = read
            entry["members_skipped"] = [
                {
                    "member": str(m.get("member") or ""),
                    "reason": SKIP_CHECKS.get(str(m.get("check")), str(m.get("check") or "")),
                }
                for m in c.get("skipped", [])
            ]
            entry["members_no_text"] = max(0, read - with_text.get(d.id, 0))
        if c.get("failed"):
            entry["parts_failed"] = len(c["failed"])
        out.append(entry)
    return out
