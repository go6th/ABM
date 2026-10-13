"""Every read and write of the research file tables (one writer module). All calls run inside a
tenant transaction (``tenant_txn``), so row level security scopes every statement.

GUARANTEE(test_a_unit_its_findings_and_the_checkpoint_commit_together)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import zstandard
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from research_files.checks import Finding

_ZC = zstandard.ZstdCompressor(level=3)
_ZD = zstandard.ZstdDecompressor()


class LeaseLost(RuntimeError):
    """Another worker owns the file now (or its lease expired and was taken)."""


def _j(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


# ------------------------------------------------------------------ uploads


async def create_upload(
    s: AsyncSession,
    *,
    name: str,
    size: int,
    mime: str | None,
    part_size: int,
    parts_total: int,
    user: str,
    expires_at: datetime,
) -> UUID:
    """A new open upload."""
    row: Any = (
        await s.execute(
            text(
                "INSERT INTO research_file_uploads (tenant_id, name, size_bytes, mime, part_size,"
                " parts_total, uploaded_by, expires_at) VALUES ((SELECT app_tenant()), :n, :sz,"
                " :m, :ps, :pt, :u, :exp) RETURNING id"
            ),
            {
                "n": name,
                "sz": size,
                "m": mime,
                "ps": part_size,
                "pt": parts_total,
                "u": user,
                "exp": expires_at,
            },
        )
    ).scalar_one()
    return UUID(str(row))


async def get_upload(s: AsyncSession, upload_id: UUID) -> Any:
    """The upload row, or None."""
    return (
        await s.execute(text("SELECT * FROM research_file_uploads WHERE id = :u"), {"u": upload_id})
    ).first()


async def parts(s: AsyncSession, upload_id: UUID) -> list[Any]:
    """Received parts in order."""
    return list(
        (
            await s.execute(
                text(
                    "SELECT part_no, size_bytes, sha256 FROM research_file_upload_parts"
                    " WHERE upload_id = :u ORDER BY part_no"
                ),
                {"u": upload_id},
            )
        ).all()
    )


async def record_part(s: AsyncSession, upload_id: UUID, part_no: int, size: int, sha: str) -> None:
    """Record (or replace) one received part."""
    await s.execute(
        text(
            "INSERT INTO research_file_upload_parts (tenant_id, upload_id, part_no, size_bytes,"
            " sha256) VALUES ((SELECT app_tenant()), :u, :n, :sz, :h)"
            " ON CONFLICT (upload_id, part_no) DO UPDATE SET size_bytes = EXCLUDED.size_bytes,"
            " sha256 = EXCLUDED.sha256, created_at = now()"
        ),
        {"u": upload_id, "n": part_no, "sz": size, "h": sha},
    )


async def finish_upload(s: AsyncSession, upload_id: UUID, file_id: UUID) -> None:
    """Mark the upload completed and link its file."""
    await s.execute(
        text("UPDATE research_file_uploads SET status = 'completed', file_id = :f WHERE id = :u"),
        {"u": upload_id, "f": file_id},
    )
    await s.execute(
        text("DELETE FROM research_file_upload_parts WHERE upload_id = :u"), {"u": upload_id}
    )


# ------------------------------------------------------------------ files


async def file_by_sha(s: AsyncSession, sha: str) -> Any:
    """The tenant's file with this content hash, or None."""
    return (
        await s.execute(text("SELECT * FROM research_files WHERE sha256 = :h"), {"h": sha})
    ).first()


async def insert_file(
    s: AsyncSession,
    *,
    file_id: UUID,
    name: str,
    size: int,
    mime: str | None,
    detected: str,
    sha: str,
    key: str,
    user: str,
) -> bool:
    """Insert an UPLOADED file; False when the same content already exists (dedupe race)."""
    row = (
        await s.execute(
            text(
                "INSERT INTO research_files (id, tenant_id, name, size_bytes, mime, detected_type,"
                " sha256, object_key, uploaded_by) VALUES (:id, (SELECT app_tenant()), :n, :sz,"
                " :m, :d, :h, :k, :u) ON CONFLICT (tenant_id, sha256) DO NOTHING RETURNING id"
            ),
            {
                "id": file_id,
                "n": name,
                "sz": size,
                "m": mime,
                "d": detected,
                "h": sha,
                "k": key,
                "u": user,
            },
        )
    ).first()
    return row is not None


async def get_file(s: AsyncSession, file_id: UUID) -> Any:
    """The file row, or None (RLS hides other tenants' files)."""
    return (
        await s.execute(text("SELECT * FROM research_files WHERE id = :f"), {"f": file_id})
    ).first()


async def list_files(s: AsyncSession, limit: int = 200) -> list[Any]:
    """The tenant's files, newest first."""
    return list(
        (
            await s.execute(
                text(
                    "SELECT id, name, size_bytes, detected_type, status, stop_reason, units_done,"
                    " created_at, updated_at FROM research_files ORDER BY created_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    )


async def acquire(
    s: AsyncSession, file_id: UUID, owner: str, now: datetime, ttl_s: int, statuses: Sequence[str]
) -> Any:
    """Take the processing lease (status moves to PARSING); None when another live owner has it
    or the file is not in one of ``statuses``."""
    return (
        await s.execute(
            text(
                "UPDATE research_files SET lease_owner = :o, lease_expires_at = :exp,"
                " heartbeat_at = :now, status = 'PARSING', updated_at = :now"
                " WHERE id = :f AND status = ANY(:st)"
                " AND (lease_owner IS NULL OR lease_owner = :o OR lease_expires_at < :now)"
                " RETURNING *"
            ),
            {
                "f": file_id,
                "o": owner,
                "now": now,
                "exp": now + timedelta(seconds=ttl_s),
                "st": list(statuses),
            },
        )
    ).first()


async def release(s: AsyncSession, file_id: UUID, owner: str) -> None:
    """Give the lease up."""
    await s.execute(
        text(
            "UPDATE research_files SET lease_owner = NULL, lease_expires_at = NULL"
            " WHERE id = :f AND lease_owner = :o"
        ),
        {"f": file_id, "o": owner},
    )


async def set_cursor(
    s: AsyncSession, file_id: UUID, owner: str, cursor: dict[str, Any], now: datetime, ttl: int
) -> None:
    """Store a cursor flag without a unit (for example: the hash was verified)."""
    updated = await s.execute(
        text(
            "UPDATE research_files SET cursor = CAST(:c AS jsonb), heartbeat_at = :now,"
            " lease_expires_at = :exp, updated_at = :now WHERE id = :f AND lease_owner = :o"
        ),
        {
            "f": file_id,
            "o": owner,
            "c": _j(cursor),
            "now": now,
            "exp": now + timedelta(seconds=ttl),
        },
    )
    if updated.rowcount == 0:  # type: ignore[attr-defined]
        raise LeaseLost(str(file_id))


async def write_unit(
    s: AsyncSession,
    *,
    file_id: UUID,
    owner: str,
    seq: int,
    kind: str,
    location: dict[str, Any],
    content: str,
    content_hash: str,
    stats: dict[str, Any],
    findings: Sequence[Finding],
    cursor: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
    ttl: int,
) -> None:
    """Write-ahead: the unit, its findings, the checkpoint and the cursor in ONE transaction (the
    caller's). ``LeaseLost`` when this worker no longer owns the file or the unit was already
    written (the sequence moved)."""
    moved = await s.execute(
        text(
            "UPDATE research_files SET units_done = :seq, cursor = CAST(:c AS jsonb),"
            " heartbeat_at = :now, lease_expires_at = :exp, updated_at = :now"
            " WHERE id = :f AND lease_owner = :o AND units_done = :prev"
        ),
        {
            "f": file_id,
            "o": owner,
            "seq": seq,
            "prev": seq - 1,
            "c": _j(cursor),
            "now": now,
            "exp": now + timedelta(seconds=ttl),
        },
    )
    if moved.rowcount == 0:  # type: ignore[attr-defined]
        raise LeaseLost(str(file_id))
    await s.execute(
        text(
            "INSERT INTO research_file_chunks (tenant_id, file_id, seq, kind, location, content,"
            " content_hash, stats) VALUES ((SELECT app_tenant()), :f, :seq, :k,"
            " CAST(:loc AS jsonb), :c, :h, CAST(:st AS jsonb))"
        ),
        {
            "f": file_id,
            "seq": seq,
            "k": kind,
            "loc": _j(location),
            "c": content,
            "h": content_hash,
            "st": _j(stats),
        },
    )
    await insert_findings(s, file_id, seq, findings)
    await save_state(s, file_id, state)


async def insert_findings(
    s: AsyncSession, file_id: UUID, seq: int, findings: Sequence[Finding]
) -> None:
    """Audit rows (the report never changes the user's data)."""
    for f in findings:
        await s.execute(
            text(
                "INSERT INTO research_file_findings (tenant_id, file_id, seq, severity, check_id,"
                " location, message, fix) VALUES ((SELECT app_tenant()), :f, :seq, :sev, :c,"
                " CAST(:loc AS jsonb), :m, :fix)"
            ),
            {
                "f": file_id,
                "seq": seq,
                "sev": f.severity,
                "c": f.check,
                "loc": _j(f.location),
                "m": f.message[:1000],
                "fix": f.fix,
            },
        )


async def save_state(s: AsyncSession, file_id: UUID, state: dict[str, Any]) -> None:
    """Upsert the compressed checkpoint."""
    await s.execute(
        text(
            "INSERT INTO research_file_state (tenant_id, file_id, state) VALUES"
            " ((SELECT app_tenant()), :f, :b) ON CONFLICT (file_id) DO UPDATE SET"
            " state = EXCLUDED.state, updated_at = now()"
        ),
        {"f": file_id, "b": _ZC.compress(_j(state).encode())},
    )


async def load_state(s: AsyncSession, file_id: UUID) -> dict[str, Any]:
    """The checkpoint, or an empty state."""
    raw = (
        await s.execute(
            text("SELECT state FROM research_file_state WHERE file_id = :f"), {"f": file_id}
        )
    ).scalar_one_or_none()
    if raw is None:
        return {}
    data: dict[str, Any] = json.loads(_ZD.decompress(bytes(raw)))
    return data


async def finish(
    s: AsyncSession,
    file_id: UUID,
    *,
    status: str,
    reason: str | None = None,
    detail: str | None = None,
    analytics: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
    release_owner: str | None = None,
) -> None:
    """Set the status (and optionally the analytics and the estimate); release the lease."""
    await s.execute(
        text(
            "UPDATE research_files SET status = :st, stop_reason = :r,"
            " stop_detail = :d, analytics = COALESCE(CAST(:a AS jsonb), analytics),"
            " estimate = COALESCE(CAST(:e AS jsonb), estimate), updated_at = now(),"
            " lease_owner = CASE WHEN CAST(:o AS text) IS NULL THEN lease_owner ELSE NULL END,"
            " lease_expires_at ="
            " CASE WHEN CAST(:o AS text) IS NULL THEN lease_expires_at ELSE NULL END"
            " WHERE id = :f"
        ),
        {
            "f": file_id,
            "st": status,
            "r": reason,
            "d": detail[:600] if detail else None,
            "a": _j(analytics) if analytics is not None else None,
            "e": _j(estimate) if estimate is not None else None,
            "o": release_owner,
        },
    )


async def set_analytics(s: AsyncSession, file_id: UUID, analytics: dict[str, Any]) -> None:
    """Replace the analytics document."""
    await s.execute(
        text(
            "UPDATE research_files SET analytics = CAST(:a AS jsonb), updated_at = now()"
            " WHERE id = :f"
        ),
        {"f": file_id, "a": _j(analytics)},
    )


async def approve(s: AsyncSession, file_id: UUID) -> bool:
    """Record the approval of the model step; False when the file is unknown."""
    updated = await s.execute(
        text(
            "UPDATE research_files SET analysis_approved = true, status = 'PARSING',"
            " updated_at = now() WHERE id = :f AND status IN ('AUDITED', 'PARTIAL')"
        ),
        {"f": file_id},
    )
    return bool(updated.rowcount)  # type: ignore[attr-defined]


async def reopen(s: AsyncSession, file_id: UUID) -> bool:
    """A PARTIAL or FAILED file goes back to PARSING for a resume."""
    updated = await s.execute(
        text(
            "UPDATE research_files SET status = 'PARSING', updated_at = now(),"
            " lease_owner = NULL, lease_expires_at = NULL"
            " WHERE id = :f AND status IN ('PARTIAL', 'FAILED', 'AUDITED')"
        ),
        {"f": file_id},
    )
    return bool(updated.rowcount)  # type: ignore[attr-defined]


async def chunks(s: AsyncSession, file_id: UUID, kinds: Sequence[str]) -> list[Any]:
    """Stored units of the given kinds, in order."""
    return list(
        (
            await s.execute(
                text(
                    "SELECT seq, kind, location, content, content_hash, stats"
                    " FROM research_file_chunks WHERE file_id = :f AND kind = ANY(:k) ORDER BY seq"
                ),
                {"f": file_id, "k": list(kinds)},
            )
        ).all()
    )


async def search(s: AsyncSession, file_id: UUID, query: str, limit: int) -> list[Any]:
    """Full-text ranked units (``websearch_to_tsquery``; falls back to any of the words)."""
    rows = list(
        (
            await s.execute(
                text(
                    "SELECT seq, kind, location, content, content_hash,"
                    " ts_rank(tsv, q) AS rank FROM research_file_chunks,"
                    " websearch_to_tsquery('simple', :q) q WHERE file_id = :f AND tsv @@ q"
                    " ORDER BY rank DESC, seq LIMIT :n"
                ),
                {"f": file_id, "q": query, "n": limit},
            )
        ).all()
    )
    if rows:
        return rows
    words = [w for w in "".join(c if c.isalnum() else " " for c in query).split() if len(w) > 2]
    if not words:
        return []
    return list(
        (
            await s.execute(
                text(
                    "SELECT seq, kind, location, content, content_hash,"
                    " ts_rank(tsv, q) AS rank FROM research_file_chunks,"
                    " to_tsquery('simple', :q) q WHERE file_id = :f AND tsv @@ q"
                    " ORDER BY rank DESC, seq LIMIT :n"
                ),
                {"f": file_id, "q": " | ".join(words[:20]), "n": limit},
            )
        ).all()
    )


async def findings(
    s: AsyncSession, file_id: UUID, severity: str | None, limit: int, offset: int
) -> tuple[list[Any], dict[str, int]]:
    """A page of findings (errors first) and the counts by severity."""
    rows = list(
        (
            await s.execute(
                text(
                    "SELECT severity, check_id, location, message, fix FROM research_file_findings"
                    " WHERE file_id = :f AND (CAST(:sev AS text) IS NULL OR severity = :sev)"
                    " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, id"
                    " LIMIT :n OFFSET :o"
                ),
                {"f": file_id, "sev": severity, "n": limit, "o": offset},
            )
        ).all()
    )
    counts = {
        str(r.severity): int(r.n)
        for r in (
            await s.execute(
                text(
                    "SELECT severity, count(*) AS n FROM research_file_findings WHERE file_id = :f"
                    " GROUP BY severity"
                ),
                {"f": file_id},
            )
        ).all()
    }
    return rows, counts


# ------------------------------------------------------------------ model calls


async def cached_reply(s: AsyncSession, key: str) -> Any:
    """The newest model reply for this cache key (any file of the tenant), or None."""
    return (
        await s.execute(
            text(
                "SELECT reply_text, model, input_tokens, output_tokens, cost_usd"
                " FROM research_file_model_calls WHERE cache_key = :k AND served_from = 'model'"
                " AND reply_text IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ),
            {"k": key},
        )
    ).first()


async def log_call(
    s: AsyncSession,
    *,
    file_id: UUID | None,
    task: str,
    model: str,
    key: str,
    served_from: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: Decimal | None = None,
    reply: str | None = None,
) -> None:
    """One model call row (``started`` before the request, then ``model`` or ``cache``)."""
    await s.execute(
        text(
            "INSERT INTO research_file_model_calls (tenant_id, file_id, task, model, cache_key,"
            " served_from, input_tokens, output_tokens, cost_usd, reply_text) VALUES"
            " ((SELECT app_tenant()), :f, :t, :m, :k, :sf, :i, :o, :c, :r)"
        ),
        {
            "f": file_id,
            "t": task,
            "m": model,
            "k": key,
            "sf": served_from,
            "i": input_tokens,
            "o": output_tokens,
            "c": cost,
            "r": reply,
        },
    )


async def spend(s: AsyncSession, file_id: UUID) -> dict[str, Any]:
    """Tokens and cost of the file's model calls by how they were served."""
    rows = (
        await s.execute(
            text(
                "SELECT served_from, count(*) AS n, sum(input_tokens) AS i,"
                " sum(output_tokens) AS o, sum(cost_usd) AS c FROM research_file_model_calls"
                " WHERE file_id = :f GROUP BY served_from"
            ),
            {"f": file_id},
        )
    ).all()
    return {
        str(r.served_from): {
            "calls": int(r.n),
            "input_tokens": int(r.i or 0),
            "output_tokens": int(r.o or 0),
            "cost_usd": float(r.c or 0),
        }
        for r in rows
    }


# ------------------------------------------------------------------ delete


async def delete_file(s: AsyncSession, file_id: UUID, user: str) -> Any:
    """Remove the file and every derived row; record one deletion event. The file row, or None."""
    row = await get_file(s, file_id)
    if row is None:
        return None
    await s.execute(
        text(
            "INSERT INTO research_file_deletions (tenant_id, file_id, sha256, size_bytes,"
            " deleted_by) VALUES ((SELECT app_tenant()), :f, :h, :sz, :u)"
        ),
        {"f": file_id, "h": row.sha256, "sz": row.size_bytes, "u": user},
    )
    await s.execute(
        text("DELETE FROM research_file_model_calls WHERE file_id = :f"), {"f": file_id}
    )
    await s.execute(text("DELETE FROM research_file_uploads WHERE file_id = :f"), {"f": file_id})
    await s.execute(text("DELETE FROM research_files WHERE id = :f"), {"f": file_id})
    return row
