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

from collections.abc import Collection, Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

from documents import repo

MEMBER = "coalesce(p.location->>'member', p.location->'from'->>'member')"
LIMIT = 200
# members that stored text, per indexed archive: counted once per process and document version
# (an indexed document never changes), not on every question (audit 2026-11-02: the scan is
# O(passages) and ran for every Ask turn)
_WITH_TEXT: dict[tuple[UUID, str], int] = {}
_WITH_TEXT_MAX = 512


async def coverage(session: Any, accounts: Sequence[UUID]) -> list[dict[str, Any]]:
    """One entry per uploaded file of these accounts, newest first: its name, status, and for an
    archive the members read, skipped (name and reason), and read without any text."""
    if not accounts:
        return []
    from documents.service import MESSAGES, SKIP_CHECKS

    docs = (
        await session.execute(
            text(
                "SELECT d.id, d.name, d.status, d.detected_type, d.reject_reason, d.stop_detail,"
                " d.passages, d.updated_at, count(*) OVER () AS total"
                " FROM documents d WHERE d.account_id = ANY(:a) AND EXISTS (SELECT 1 FROM"
                " accounts x WHERE x.id = d.account_id AND x.deleted_at IS NULL)"
                " ORDER BY d.created_at DESC, d.id LIMIT :n"
            ),
            {"a": list(accounts), "n": LIMIT},
        )
    ).all()
    counts = await repo.part_counts(session, [d.id for d in docs])
    stamp = {d.id: str(d.updated_at) for d in docs}
    archives = [
        d.id
        for d in docs
        if counts.get(d.id, {}).get("members_read")
        and d.status == "indexed"
        and (d.id, stamp[d.id]) not in _WITH_TEXT
    ]
    if archives:
        found = await session.execute(
            text(
                f"SELECT p.document_id, count(DISTINCT {MEMBER}) AS n FROM document_passages p"  # nosec B608 -- fixed expression.
                f" WHERE p.document_id = ANY(:ids) AND {MEMBER} IS NOT NULL GROUP BY 1"
            ),
            {"ids": archives},
        )
        for r in found:
            if len(_WITH_TEXT) >= _WITH_TEXT_MAX:
                _WITH_TEXT.pop(next(iter(_WITH_TEXT)))
            _WITH_TEXT[(r.document_id, stamp[r.document_id])] = int(r.n)
    with_text = {k[0]: n for k, n in _WITH_TEXT.items() if stamp.get(k[0]) == k[1]}
    out = []
    for d in docs:
        c = counts.get(d.id, {})
        read = int(c.get("members_read", 0))
        entry: dict[str, Any] = {
            "document": str(d.name or "an uploaded file"),
            "document_id": str(d.id),
            "status": str(d.status),
            "type": str(d.detected_type or ""),
            "passages": int(c.get("passages") or d.passages or 0),
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
            entry["members_skipped_total"] = max(
                int(c.get("skipped_total", 0)), len(entry["members_skipped"])
            )
            if d.id in with_text:
                entry["members_no_text"] = max(0, read - with_text[d.id])
        if c.get("failed"):
            entry["parts_failed"] = len(c["failed"])
        out.append(entry)
    total = int(docs[0].total) if docs else 0
    if total > len(docs):
        out.append({"document": f"{total - len(docs)} older files", "status": "omitted"})
    return out


def as_sources(
    entries: Sequence[Mapping[str, Any]], table_documents: Collection[Any] = ()
) -> list[dict[str, Any]]:
    """The indexed files that hold text but no table, as the file map lists them (one per file;
    a file's tables are listed as tables and an archive has its coverage line). Names and counts
    from the same cheap read as the coverage: no passage scan."""
    held = {str(d) for d in table_documents}
    return [
        {
            "document": e["document"],
            "shape": "transcript" if e.get("type") in ("vtt", "srt") else "prose",
            "passages": e.get("passages", 0),
        }
        for e in entries
        if e.get("status") == "indexed"
        and e.get("passages")
        and "members_read" not in e
        and e.get("document_id") not in held
    ]
