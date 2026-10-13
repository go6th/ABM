"""Every SQL statement on the document tables and the private ``chunks`` index (single writer).

All functions run in the caller's ``tenant_txn``: RLS limits every statement to the tenant, and
inserts take the tenant from ``app_tenant()``, never from an argument.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from documents.chunking import Passage
from documents.embed import literal

_COLS = (
    "id, account_id, name, declared_bytes, size_bytes, declared_type, detected_type, sha256,"
    " quarantine_key, upload_token_sha256, object_key, status, reject_reason, stop_detail,"
    " redaction_tier, authoritative, detected_company, passages, redactions, uploaded_by,"
    " upload_expires_at, created_at, updated_at"
)


def _j(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


async def policy_tier(s: AsyncSession, default: str) -> str:
    """The tenant's redaction tier (``default`` until an admin chose one)."""
    tier = (await s.execute(text("SELECT redaction_tier FROM document_policies"))).scalar()
    return str(tier or default)


async def set_policy(s: AsyncSession, tier: str, actor: str) -> None:
    """Set the tenant's redaction tier (applies to documents parsed afterwards)."""
    await s.execute(
        text(
            "INSERT INTO document_policies (tenant_id, redaction_tier, updated_by)"
            " VALUES ((SELECT app_tenant()), :t, :a) ON CONFLICT (tenant_id) DO UPDATE"
            " SET redaction_tier = EXCLUDED.redaction_tier, updated_by = EXCLUDED.updated_by,"
            " updated_at = now()"
        ),
        {"t": tier, "a": actor},
    )


async def create(
    s: AsyncSession,
    *,
    document_id: UUID,
    account_id: UUID | None,
    name: str,
    declared_bytes: int,
    declared_type: str,
    quarantine_key: str,
    token_sha256: str,
    uploaded_by: str,
    expires_at: datetime,
) -> None:
    """A document row awaiting its bytes (quarantine key and one-time upload token hash)."""
    await s.execute(
        text(
            "INSERT INTO documents (id, tenant_id, account_id, name, declared_bytes,"
            " declared_type, quarantine_key, upload_token_sha256, uploaded_by,"
            " upload_expires_at) VALUES (:id, (SELECT app_tenant()), :a, :n, :b, :t, :q, :tok,"
            " :u, :e)"
        ),
        {
            "id": document_id,
            "a": account_id,
            "n": name,
            "b": declared_bytes,
            "t": declared_type,
            "q": quarantine_key,
            "tok": token_sha256,
            "u": uploaded_by,
            "e": expires_at,
        },
    )


async def get(s: AsyncSession, document_id: UUID, *, lock: bool = False) -> Any:
    """One document of this tenant (None when absent or another tenant's)."""
    sql = f"SELECT {_COLS} FROM documents WHERE id = :d" + (" FOR UPDATE" if lock else "")  # nosec B608 -- fixed return columns.
    return (await s.execute(text(sql), {"d": document_id})).first()


async def many(s: AsyncSession, ids: Sequence[UUID]) -> list[Any]:
    """The documents among ``ids`` that belong to this tenant."""
    if not ids:
        return []
    rows = await s.execute(
        text(f"SELECT {_COLS} FROM documents WHERE id = ANY(:ids) ORDER BY created_at, id"),  # nosec B608 -- fixed return columns.
        {"ids": list(ids)},
    )
    return list(rows.all())


async def list_for(s: AsyncSession, account_id: UUID | None, limit: int = 200) -> list[Any]:
    """The tenant's documents, newest first (one account's when ``account_id`` is given)."""
    where = "WHERE account_id = :a" if account_id is not None else ""
    rows = await s.execute(
        text(f"SELECT {_COLS} FROM documents {where} ORDER BY created_at DESC, id LIMIT :l"),  # nosec B608 -- fixed columns and a fixed filter.
        {"a": account_id, "l": limit},
    )
    return list(rows.all())


async def set_status(s: AsyncSession, document_id: UUID, status: str, **fields: Any) -> None:
    """Move a document to ``status`` and set the named columns (JSON columns as dicts)."""
    allowed = {
        "size_bytes", "detected_type", "sha256", "object_key", "stop_detail", "redaction_tier",
        "detected_company", "passages", "redactions", "account_id", "upload_token_sha256",
    }  # fmt: skip
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"not settable: {sorted(bad)}")
    sets = ["status = :st", "updated_at = now()"]
    params: dict[str, Any] = {"d": document_id, "st": status}
    for name, value in fields.items():
        if name == "detected_company":
            sets.append("detected_company = CAST(:detected_company AS jsonb)")
            params[name] = None if value is None else _j(value)
        else:
            sets.append(f"{name} = :{name}")
            params[name] = value
    await s.execute(text(f"UPDATE documents SET {', '.join(sets)} WHERE id = :d"), params)  # nosec B608 -- columns from the allow-list above.


async def reject(s: AsyncSession, document_id: UUID, reason: str, detail: str | None) -> None:
    """End a document ``rejected`` (never parsed) with its reason."""
    await s.execute(
        text(
            "UPDATE documents SET status = 'rejected', reject_reason = :r, stop_detail = :dt,"
            " lease_owner = NULL, lease_expires_at = NULL, upload_token_sha256 = NULL,"
            " updated_at = now() WHERE id = :d"
        ),
        {"d": document_id, "r": reason, "dt": (detail or None) and detail[:600]},
    )


async def claim(
    s: AsyncSession, document_id: UUID, owner: str, ttl_s: int, statuses: Sequence[str]
) -> Any:
    """Take the processing lease of a document in one of ``statuses`` (None: not claimable)."""
    return (
        await s.execute(
            text(
                f"UPDATE documents SET lease_owner = :o, lease_expires_at = now() + make_interval("  # nosec B608 -- fixed return columns.
                f"secs => :ttl), updated_at = now() WHERE id = :d AND status = ANY(:st) AND"
                f" (lease_owner IS NULL OR lease_owner = :o OR lease_expires_at < now())"
                f" RETURNING {_COLS}"
            ),
            {"d": document_id, "o": owner, "ttl": ttl_s, "st": list(statuses)},
        )
    ).first()


async def release(s: AsyncSession, document_id: UUID, owner: str) -> None:
    """Give the lease back."""
    await s.execute(
        text(
            "UPDATE documents SET lease_owner = NULL, lease_expires_at = NULL WHERE id = :d"
            " AND lease_owner = :o"
        ),
        {"d": document_id, "o": owner},
    )


async def clear_passages(s: AsyncSession, document_id: UUID) -> None:
    """Remove a document's passages, redaction map and index rows (a re-parse starts clean)."""
    await s.execute(text("DELETE FROM chunks WHERE doc_id = :d"), {"d": document_id})
    await s.execute(
        text("DELETE FROM document_redactions WHERE document_id = :d"), {"d": document_id}
    )
    await s.execute(
        text("DELETE FROM document_passages WHERE document_id = :d"), {"d": document_id}
    )


async def write_passages(
    s: AsyncSession,
    document_id: UUID,
    account_id: UUID | None,
    passages: Sequence[Passage],
    redactions: Sequence[tuple[int, str, str, str]],
) -> None:
    """Insert passages and the redaction map rows ``(passage seq, placeholder, kind, original)``."""
    for p in passages:
        await s.execute(
            text(
                "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind,"
                " location, heading_path, content, content_sha256, token_count) VALUES"
                " ((SELECT app_tenant()), :a, :d, :seq, :k, CAST(:loc AS jsonb), :h, :c, :sha,"
                " :tok)"
            ),
            {
                "a": account_id,
                "d": document_id,
                "seq": p.seq,
                "k": p.kind,
                "loc": _j(p.location),
                "h": p.heading_path,
                "c": p.text,
                "sha": p.sha256,
                "tok": p.tokens,
            },
        )
    for seq, placeholder, kind, original in redactions:
        await s.execute(
            text(
                "INSERT INTO document_redactions (tenant_id, account_id, document_id,"
                " passage_seq, placeholder, kind, original) VALUES ((SELECT app_tenant()), :a,"
                " :d, :seq, :ph, :k, :o)"
            ),
            {
                "a": account_id,
                "d": document_id,
                "seq": seq,
                "ph": placeholder,
                "k": kind,
                "o": original[:500],
            },
        )


async def bind_account(s: AsyncSession, document_ids: Sequence[UUID], account_id: UUID) -> int:
    """Bind unbound documents (and their passages and map) to the confirmed account."""
    res = await s.execute(
        text(
            "UPDATE documents SET account_id = :a, updated_at = now() WHERE id = ANY(:ids)"
            " AND (account_id IS NULL OR account_id = :a)"
        ),
        {"a": account_id, "ids": list(document_ids)},
    )
    for table in ("document_passages", "document_redactions"):
        await s.execute(
            text(f"UPDATE {table} SET account_id = :a WHERE document_id = ANY(:ids)"),  # nosec B608 -- table from a fixed tuple.
            {"a": account_id, "ids": list(document_ids)},
        )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def passages(s: AsyncSession, document_id: UUID) -> list[Any]:
    """A document's passages in order."""
    rows = await s.execute(
        text(
            "SELECT seq, kind, location, heading_path, content, content_sha256, token_count,"
            " account_id FROM document_passages WHERE document_id = :d ORDER BY seq"
        ),
        {"d": document_id},
    )
    return list(rows.all())


async def index_passages(
    s: AsyncSession,
    document_id: UUID,
    account_id: UUID,
    rows: Sequence[Any],
    vectors: Sequence[Sequence[float]],
    model: str,
) -> bool:
    """Write passages and their vectors to the private ``chunks`` index (visibility private).
    The document row is locked first: a document deleted meanwhile writes nothing (False), so a
    deletion during indexing never leaves index rows behind."""
    alive = (
        await s.execute(
            text("SELECT 1 FROM documents WHERE id = :d FOR UPDATE"), {"d": document_id}
        )
    ).first()
    if alive is None:
        return False
    await s.execute(text("DELETE FROM chunks WHERE doc_id = :d"), {"d": document_id})
    for row, vec in zip(rows, vectors, strict=True):
        await s.execute(
            text(
                "INSERT INTO chunks (tenant_id, account_id, doc_id, visibility, content,"
                " embedding, passage_seq, location, heading_path, embedding_model) VALUES"
                " ((SELECT app_tenant()), :a, :d, 'private', :c, CAST(:v AS halfvec), :seq,"
                " CAST(:loc AS jsonb), :h, :m)"
            ),
            {
                "a": account_id,
                "d": document_id,
                "c": row.content,
                "v": literal(vec),
                "seq": row.seq,
                "loc": _j(row.location),
                "h": row.heading_path,
                "m": model,
            },
        )
    return True


async def link_run(s: AsyncSession, run_id: UUID, account_id: UUID, ids: Sequence[UUID]) -> None:
    """The documents a file-mode run researches."""
    for doc in ids:
        await s.execute(
            text(
                "INSERT INTO run_documents (tenant_id, account_id, run_id, document_id) VALUES"
                " ((SELECT app_tenant()), :a, :r, :d) ON CONFLICT DO NOTHING"
            ),
            {"a": account_id, "r": run_id, "d": doc},
        )


async def run_documents(s: AsyncSession, run_id: UUID) -> list[Any]:
    """The documents of a run (deleted documents are gone from the list)."""
    rows = await s.execute(
        text(
            f"SELECT {', '.join('d.' + c.strip() for c in _COLS.split(','))} FROM run_documents rd"  # nosec B608 -- fixed return columns.
            " JOIN documents d ON d.id = rd.document_id WHERE rd.run_id = :r"
            " ORDER BY d.created_at, d.id"
        ),
        {"r": run_id},
    )
    return list(rows.all())


async def run_passages(s: AsyncSession, run_id: UUID) -> list[Any]:
    """Every passage of a run's documents, in document then passage order."""
    rows = await s.execute(
        text(
            "SELECT p.document_id, d.name AS document_name, d.authoritative, p.seq, p.kind,"
            " p.location, p.heading_path, p.content FROM run_documents rd"
            " JOIN documents d ON d.id = rd.document_id"
            " JOIN document_passages p ON p.document_id = d.id"
            " WHERE rd.run_id = :r ORDER BY d.created_at, d.id, p.seq"
        ),
        {"r": run_id},
    )
    return list(rows.all())


async def private_texts(s: AsyncSession, account_id: UUID) -> list[str]:
    """The account's private passage texts (the leak check compares against them)."""
    rows = await s.execute(
        text("SELECT content FROM document_passages WHERE account_id = :a ORDER BY id"),
        {"a": account_id},
    )
    return [r.content for r in rows]


async def nearest_private(s: AsyncSession, account_id: UUID, vec: Sequence[float]) -> float:
    """The highest cosine similarity of ``vec`` to the account's private index (0 when empty)."""
    value = (
        await s.execute(
            text(
                "SELECT 1 - (embedding <=> CAST(:v AS halfvec)) AS sim FROM chunks"
                " WHERE account_id = :a ORDER BY embedding <=> CAST(:v AS halfvec) LIMIT 1"
            ),
            {"a": account_id, "v": literal(vec)},
        )
    ).scalar()
    return float(value or 0.0)


async def delete(s: AsyncSession, document_id: UUID, user: str) -> Any:
    """Remove a document's index rows, passages, map and row; keep an insert-only deletion
    record. Returns the deleted row (None when absent). Facts are marked by the caller."""
    row = await get(s, document_id, lock=True)
    if row is None:
        return None
    chunks = (
        await s.execute(text("DELETE FROM chunks WHERE doc_id = :d"), {"d": document_id})
    ).rowcount  # type: ignore[attr-defined]
    await s.execute(text("DELETE FROM documents WHERE id = :d"), {"d": document_id})
    return row, int(chunks or 0)


async def record_deletion(
    s: AsyncSession, row: Any, chunks: int, facts_marked: int, user: str
) -> None:
    """The insert-only deletion record (hash and size only)."""
    await s.execute(
        text(
            "INSERT INTO document_deletions (tenant_id, account_id, document_id, sha256,"
            " size_bytes, chunks, facts_marked, deleted_by) VALUES ((SELECT app_tenant()), :a,"
            " :d, :sha, :b, :c, :f, :u)"
        ),
        {
            "a": row.account_id,
            "d": row.id,
            "sha": row.sha256,
            "b": row.size_bytes,
            "c": chunks,
            "f": facts_marked,
            "u": user,
        },
    )


async def set_authoritative(s: AsyncSession, document_id: UUID, value: bool) -> bool:
    """Mark a document authoritative (admin): its facts may be Confirmed (still private)."""
    res = await s.execute(
        text("UPDATE documents SET authoritative = :v, updated_at = now() WHERE id = :d"),
        {"d": document_id, "v": value},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


async def tenant_domains(s: AsyncSession) -> list[str]:
    """The workspace's own email domains (its users), never a research target."""
    rows = await s.execute(
        text(
            "SELECT DISTINCT lower(split_part(email, '@', 2)) AS d FROM users"
            " WHERE email LIKE '%@%'"
        )
    )
    return [r.d for r in rows if r.d]


async def record_gate(
    s: AsyncSession, run_id: UUID, account_id: UUID, verdict: str, defects: list[dict[str, Any]]
) -> None:
    """The pre-delivery gate's verdict (insert-only)."""
    await s.execute(
        text(
            "INSERT INTO run_gate_results (tenant_id, account_id, run_id, verdict, defects)"
            " VALUES ((SELECT app_tenant()), :a, :r, :v, CAST(:d AS jsonb))"
        ),
        {"a": account_id, "r": run_id, "v": verdict, "d": _j(defects)},
    )


async def record_leak_check(
    s: AsyncSession,
    *,
    run_id: UUID,
    account_id: UUID,
    target: str,
    verdict: str,
    ngram_hits: int,
    embedding_hits: int,
    max_similarity: float,
    passages_checked: int,
    hits: list[dict[str, Any]],
) -> None:
    """One leak check (fingerprints and scores only, never text; insert-only)."""
    await s.execute(
        text(
            "INSERT INTO leak_checks (tenant_id, account_id, run_id, target, verdict, ngram_hits,"
            " embedding_hits, max_similarity, passages_checked, hits) VALUES"
            " ((SELECT app_tenant()), :a, :r, :t, :v, :n, :e, :m, :p, CAST(:h AS jsonb))"
        ),
        {
            "a": account_id,
            "r": run_id,
            "t": target[:200],
            "v": verdict,
            "n": ngram_hits,
            "e": embedding_hits,
            "m": round(min(max(max_similarity, 0.0), 1.0), 5),
            "p": passages_checked,
            "h": _j(hits[:50]),
        },
    )


async def store_internal(
    s: AsyncSession, run_id: UUID, account_id: UUID, label: str, key: str, sha: str, size: int
) -> None:
    """The run's Internal Only file (one per run)."""
    await s.execute(
        text(
            "INSERT INTO internal_deliverables (tenant_id, account_id, run_id, label, object_key,"
            " sha256, size_bytes) VALUES ((SELECT app_tenant()), :a, :r, :l, :k, :sha, :b)"
            " ON CONFLICT (tenant_id, run_id) DO NOTHING"
        ),
        {"a": account_id, "r": run_id, "l": label, "k": key, "sha": sha, "b": size},
    )
