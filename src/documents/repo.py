"""Every SQL statement on the document tables and the private ``chunks`` index (single writer).

All functions run in the caller's ``tenant_txn``: RLS limits every statement to the tenant, and
inserts take the tenant from ``app_tenant()``, never from an argument.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from documents.chunking import VALUE_INDEX_LIMIT, Passage, column_aliases
from documents.embed import literal

_COLS = (
    "id, account_id, name, declared_bytes, size_bytes, declared_type, detected_type, sha256,"
    " quarantine_key, upload_token_sha256, object_key, status, reject_reason, stop_detail,"
    " redaction_tier, authoritative, detected_company, passages, redactions, uploaded_by,"
    " upload_expires_at, created_at, updated_at, parts_total, indexed_through, audience,"
    " imported_at, scan_attempts"
)
PART_SPAN = 10_000_000  # passage numbers per part: part n writes n * PART_SPAN + 1, + 2, ...
# a text column is multi-valued (a cell holds "a, b; c" items) when a fifth or more of its
# text cells hold one (documents.chunking.Column.lists tallies the cells that do)
LIST_TALLY_REQUIRED = 5


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
    """The tenant's documents, newest first (one account's when ``account_id`` is given; the
    uploads of an account the user deleted are not listed under it)."""
    where = (
        "WHERE account_id = :a AND EXISTS (SELECT 1 FROM accounts x WHERE x.id = :a"
        " AND x.deleted_at IS NULL)"
        if account_id is not None
        else ""
    )
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


async def begin_scan(s: AsyncSession, document_id: UUID) -> None:
    """Move a document to ``scanning`` and count the attempt (``scan_attempts``)."""
    await s.execute(
        text(
            "UPDATE documents SET status = 'scanning', scan_attempts = scan_attempts + 1,"
            " updated_at = now() WHERE tenant_id = (SELECT app_tenant()) AND id = :d"
        ),
        {"d": document_id},
    )


async def scan_failed(s: AsyncSession, document_id: UUID, detail: str | None) -> None:
    """End a scan attempt ``scan_failed``: the scanner could not be reached, so the file is
    neither clean nor infected. It is never read; its quarantine object is kept for a retry."""
    await s.execute(
        text(
            "UPDATE documents SET status = 'scan_failed', reject_reason = 'scan_unavailable',"
            " stop_detail = :dt, lease_owner = NULL, lease_expires_at = NULL,"
            " updated_at = now() WHERE tenant_id = (SELECT app_tenant()) AND id = :d"
        ),
        {"d": document_id, "dt": (detail or None) and detail[:600]},
    )


async def requeue_scans(
    s: AsyncSession, tenant_id: UUID, idle_before: datetime, max_attempts: int, limit: int
) -> list[UUID]:
    """``scan_failed`` documents of one client that have waited since ``idle_before`` and have
    attempts left go back to ``quarantined`` (their reason is cleared); their ids."""
    rows = await s.execute(
        text(
            "UPDATE documents SET status = 'quarantined', reject_reason = NULL, stop_detail = NULL,"
            " updated_at = now() WHERE id IN (SELECT id FROM documents WHERE tenant_id = :t"
            " AND status = 'scan_failed' AND scan_attempts < :m AND updated_at < :b"
            " ORDER BY updated_at, id LIMIT :l FOR UPDATE SKIP LOCKED)"
            " AND tenant_id = :t RETURNING id"
        ),
        {"t": tenant_id, "m": max_attempts, "b": idle_before, "l": limit},
    )
    return [UUID(str(r.id)) for r in rows.all()]


async def abandon_stale(
    s: AsyncSession, tenant_id: UUID, started_before: datetime, limit: int
) -> list[tuple[UUID, str]]:
    """``awaiting_upload`` documents of one client that began before ``started_before`` become
    ``abandoned`` (their upload token is dropped); ``(id, quarantine_key)`` of each."""
    rows = await s.execute(
        text(
            "UPDATE documents SET status = 'abandoned', upload_token_sha256 = NULL,"
            " updated_at = now() WHERE id IN (SELECT id FROM documents WHERE tenant_id = :t"
            " AND status = 'awaiting_upload' AND created_at < :b"
            " ORDER BY created_at, id LIMIT :l FOR UPDATE SKIP LOCKED)"
            " AND tenant_id = :t RETURNING id, quarantine_key"
        ),
        {"t": tenant_id, "b": started_before, "l": limit},
    )
    return [(UUID(str(r.id)), str(r.quarantine_key)) for r in rows.all()]


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
    """Remove a document's passages, redaction map, index rows and tables (a re-parse starts
    clean)."""
    await s.execute(text("DELETE FROM chunks WHERE doc_id = :d"), {"d": document_id})
    await s.execute(text("DELETE FROM document_tables WHERE document_id = :d"), {"d": document_id})
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
    ingested_at: str | None = None,
) -> None:
    """Insert passages and the redaction map rows ``(passage seq, placeholder, kind, original)``
    (one batched statement each). A passage number already written is left as it is. Every
    passage's stored metadata carries ``ingested_at`` (the document's ingest stamp the caller
    passes; B4: when the workspace read this text)."""
    if passages:
        await s.execute(
            text(
                "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind,"
                " location, heading_path, content, content_sha256, token_count, metadata) VALUES"
                " ((SELECT app_tenant()), :a, :d, :seq, :k, CAST(:loc AS jsonb), :h, :c, :sha,"
                " :tok, CAST(:md AS jsonb)) ON CONFLICT (document_id, seq) DO NOTHING"
            ),
            [
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
                    "md": _j(
                        {**p.metadata, **({"ingested_at": ingested_at} if ingested_at else {})}
                    )
                    if p.metadata or ingested_at
                    else None,
                }
                for p in passages
            ],
        )
    if redactions:
        await s.execute(
            text(
                "INSERT INTO document_redactions (tenant_id, account_id, document_id,"
                " passage_seq, placeholder, kind, original) VALUES ((SELECT app_tenant()), :a,"
                " :d, :seq, :ph, :k, :o)"
            ),
            [
                {
                    "a": account_id,
                    "d": document_id,
                    "seq": seq,
                    "ph": placeholder,
                    "k": kind,
                    "o": original[:500],
                }
                for seq, placeholder, kind, original in redactions
            ],
        )


async def bind_account(
    s: AsyncSession, document_ids: Sequence[UUID], account_id: UUID, *, rebind: bool = False
) -> int:
    """Bind unbound documents (and their passages, map, table store and zip members) to the
    confirmed account. The table store moves too: Ask lists tables by account, so a table left
    unbound is never offered and every count question falls back to search (2026-11-04).
    ``rebind`` also moves documents held as ``needs_account`` that were bound to another account
    (the user picked a different one)."""
    held = " OR status = 'needs_account'" if rebind else ""
    res = await s.execute(
        text(
            "UPDATE documents SET account_id = :a, updated_at = now()"
            " WHERE tenant_id = (SELECT app_tenant()) AND id = ANY(:ids)"
            f" AND (account_id IS NULL OR account_id = :a{held})"  # nosec B608 -- fixed text.
            " RETURNING id"
        ),
        {"a": account_id, "ids": list(document_ids)},
    )
    # only the documents whose own row moved take their passages, map, tables and members along
    moved = [UUID(str(r.id)) for r in res.all()]
    if not moved:
        return 0
    for table in (
        "document_passages",
        "document_redactions",
        "document_tables",
        "document_table_rows",
        "document_members",
    ):
        await s.execute(
            text(
                f"UPDATE {table} SET account_id = :a"  # nosec B608 -- table from a fixed tuple.
                " WHERE tenant_id = (SELECT app_tenant()) AND document_id = ANY(:ids)"
            ),
            {"a": account_id, "ids": moved},
        )
    return len(moved)


async def release_hold(s: AsyncSession, document_ids: Sequence[UUID]) -> list[UUID]:
    """Release uploads held as ``needs_account`` (now bound) back to ``parsed``; the ids moved."""
    rows = await s.execute(
        text(
            "UPDATE documents SET status = 'parsed', updated_at = now()"
            " WHERE tenant_id = (SELECT app_tenant()) AND id = ANY(:ids)"
            " AND status = 'needs_account' AND account_id IS NOT NULL RETURNING id"
        ),
        {"ids": list(document_ids)},
    )
    return [UUID(str(r.id)) for r in rows]


async def account_identity(s: AsyncSession, account_id: UUID) -> Any:
    """The domain, name and watch config of a live account of the tenant (None when absent):
    what the workspace gate compares an uploaded package with."""
    return (
        await s.execute(
            text(
                "SELECT domain, name, watch_config_json FROM accounts"
                " WHERE tenant_id = (SELECT app_tenant()) AND id = :a AND deleted_at IS NULL"
            ),
            {"a": account_id},
        )
    ).first()


async def account_domain(s: AsyncSession, account_id: UUID) -> str | None:
    """The domain of a live account of the tenant (RLS limits it to the user's own)."""
    value = (
        await s.execute(
            text(
                "SELECT domain FROM accounts WHERE tenant_id = (SELECT app_tenant())"
                " AND id = :a AND deleted_at IS NULL"
            ),
            {"a": account_id},
        )
    ).scalar_one_or_none()
    return str(value) if value else None


async def account_for_domain(
    s: AsyncSession, document_id: UUID, registrable_domain: str
) -> UUID | None:
    """The live account of the document's owner whose domain is ``registrable_domain`` (or a
    subdomain of it); None when there is none. The tenant is the session's (``app_tenant()``)."""
    row = (
        await s.execute(
            text(
                "SELECT a.id FROM accounts a JOIN documents d ON d.id = :d"
                " AND d.tenant_id = a.tenant_id AND a.owner_user_id = d.owner_user_id"
                " WHERE a.tenant_id = (SELECT app_tenant()) AND a.deleted_at IS NULL"
                " AND (a.domain = :r OR right(a.domain, length(:r) + 1) = '.' || :r)"
                " ORDER BY a.domain = :r DESC, a.id"
                " LIMIT 1"
            ),
            {"d": document_id, "r": registrable_domain},
        )
    ).first()
    return UUID(str(row.id)) if row else None


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


async def set_semantic_vectors(
    s: AsyncSession, rows: Sequence[tuple[Any, Any, str]], model: str
) -> None:
    """Give chunks their retrieval vector (``(tenant_id, chunk id, vector literal)`` each): the
    backfill for chunks indexed before the retrieval vectors existed (``documents.retrieve``)."""
    if not rows:
        return
    await s.execute(
        text(
            "UPDATE chunks SET semantic_embedding = CAST(:v AS halfvec), semantic_model = :m"
            " WHERE tenant_id = :t AND id = :i"
        ),
        [{"v": v, "m": model, "t": t, "i": i} for t, i, v in rows],
    )


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


# the document ids of run :r: the linked ones, plus the imported document of a workspace-import run
RUN_DOCUMENT_IDS = (
    "SELECT rd.document_id FROM run_documents rd WHERE rd.run_id = :r"
    " UNION SELECT CAST(x.run_input->>'document_id' AS uuid) FROM account_runs x"
    " WHERE x.id = :r AND x.finished_reason = 'file_import'"
    " AND x.run_input->>'document_id' IS NOT NULL"
)


async def run_documents(s: AsyncSession, run_id: UUID) -> list[Any]:
    """The documents of a run (deleted documents are gone from the list). A workspace-import
    run has no run_documents row: its one document is named in ``run_input``."""
    rows = await s.execute(
        text(
            f"SELECT {', '.join('d.' + c.strip() for c in _COLS.split(','))} FROM documents d"  # nosec B608 -- fixed return columns.
            f" WHERE d.id IN ({RUN_DOCUMENT_IDS}) ORDER BY d.created_at, d.id"
        ),
        {"r": run_id},
    )
    return list(rows.all())


async def document_passages_page(
    s: AsyncSession, document_id: UUID, after: int, limit: int
) -> list[Any]:
    """The next page of one document's passages by number (keyset), with the document's name and
    flag, shaped like ``run_passages`` rows (a file of any size is read a page at a time)."""
    rows = await s.execute(
        text(
            "SELECT p.document_id, d.name AS document_name, d.authoritative, p.seq, p.kind,"
            " p.location, p.heading_path, p.content FROM document_passages p"
            " JOIN documents d ON d.id = p.document_id"
            " WHERE p.document_id = :d AND p.seq > :after ORDER BY p.seq LIMIT :n"
        ),
        {"d": document_id, "after": after, "n": limit},
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


def own_deliverable_name_pattern(delivered: Iterable[str]) -> str:
    """A case-insensitive POSIX regex over a file name (``documents.name`` or a zip member's
    path) matching one of the files this product itself delivered for the account
    (``deliverables.filename``: ``Tolmar_Account_Summary.docx``, ``Tolmar_workspace.zip``),
    also under the ``name (2).ext`` suffix a browser gives a repeated download. Nothing else
    matches: a client upload merely named ``People List.xlsx`` is private material and stays
    checked (review 2026-10-22); an account with no delivery yet excludes nothing."""
    names = sorted({n.strip().lower() for n in delivered if n and n.strip()})
    if not names:
        return "(?!x)x"
    alternatives = []
    for name in names:
        stem, dot, ext = name.rpartition(".")
        if dot and stem:
            alternatives.append(re.escape(stem) + r"( \([0-9]+\))?\." + re.escape(ext))
        else:
            alternatives.append(re.escape(name) + r"( \([0-9]+\))?")
    return "(^|/)(" + "|".join(alternatives) + ")$"


# The files this product delivered for the account (never the operator-only internal file,
# whose body IS the client's private material; it lives in ``internal_deliverables`` anyway).
_DELIVERED_NAMES = (
    "SELECT filename FROM deliverables WHERE account_id = :a"
    " AND kind NOT IN ('operator_only', 'internal_only', 'internal')"
)
# The account's uploaded documents that are this product's own earlier outputs, not private
# material. The owner's refresh workflow uploads the previous workspace, and the new files share
# headings, titles and names with it by design (staging 2026-10-21: seven deliveries held back
# as "too close to your uploaded files"). Two ways a document (or a zip member) is one:
# a workbook whose rows ``documents.deliverables`` recognised (its ``file_import`` run), or a
# file carrying the name of one of the account's deliveries. A workspace zip is one document
# whose members are only visible in each passage's location (``shapes.member_of``): there the
# member's own name decides, so a delivered member is skipped while a private memo in the same
# zip stays checked.
_IMPORTED = (
    "SELECT d.id FROM documents d WHERE d.account_id = :a AND EXISTS ("
    "SELECT 1 FROM account_runs r WHERE r.account_id = :a AND r.finished_reason = 'file_import'"
    " AND r.idempotency_key = 'file-import-' || d.id::text)"
)
_NAMED = "SELECT d.id FROM documents d WHERE d.account_id = :a AND d.name ~* :pat"
_MEMBER = "COALESCE({loc}->'from'->>'member', {loc}->>'member')"


def _own_filter(doc: str, loc: str) -> str:
    """SQL (``:a`` and ``:pat`` bound) that is true for a passage or chunk of the account's
    own earlier deliverables: ``doc`` names the document id column, ``loc`` the location."""
    member = _MEMBER.format(loc=loc)
    return (
        f"({doc} IN ({_IMPORTED}) OR CASE WHEN {member} IS NULL"
        f" THEN {doc} IN ({_NAMED}) ELSE {member} ~* :pat END)"
    )


async def _delivered_names(s: AsyncSession, account_id: UUID) -> list[str]:
    rows = await s.execute(text(_DELIVERED_NAMES), {"a": account_id})
    return [str(r.filename) for r in rows]


async def private_passages(s: AsyncSession, account_id: UUID) -> list[tuple[str, str]]:
    """The account's private passages as ``(document id, text)`` (the leak check compares
    client files against them), without this product's own earlier deliverables."""
    pat = own_deliverable_name_pattern(await _delivered_names(s, account_id))
    rows = await s.execute(
        text(
            "SELECT document_id, content FROM document_passages p WHERE account_id = :a"  # nosec B608 -- fixed SQL fragments; values are bound.
            f" AND NOT {_own_filter('document_id', 'p.location')} ORDER BY id"
        ),
        {"a": account_id, "pat": pat},
    )
    return [(str(r.document_id), r.content) for r in rows]


async def nearest_private(
    s: AsyncSession, account_id: UUID, vec: Sequence[float]
) -> tuple[float, str | None]:
    """The highest cosine similarity of ``vec`` to the account's private index and the document
    it is in (``0, None`` when empty), without this product's own earlier deliverables."""
    pat = own_deliverable_name_pattern(await _delivered_names(s, account_id))
    row = (
        await s.execute(
            text(
                "SELECT 1 - (embedding <=> CAST(:v AS halfvec)) AS sim, doc_id FROM chunks"  # nosec B608 -- fixed SQL fragments; values are bound.
                f" WHERE account_id = :a AND NOT {_own_filter('doc_id', 'location')}"
                " ORDER BY embedding <=> CAST(:v AS halfvec) LIMIT 1"
            ),
            {"a": account_id, "v": literal(vec), "pat": pat},
        )
    ).first()
    if row is None:
        return 0.0, None
    return float(row.sim or 0.0), str(row.doc_id)


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


# ---------------------------------------------------------------------- parts (Phase 4B section 3)

_PART_COLS = (
    "document_id, part_no, start_cursor, end_offset, end_page, status, attempts, passages,"
    " evidence, failure, lease_owner"
)


async def platform_max_file_bytes(s: AsyncSession) -> int | None:
    """The admin's per-file limit (None when the platform row is missing)."""
    value = await s.scalar(text("SELECT max_file_bytes FROM platform_defaults WHERE id = 1"))
    return int(value) if value is not None else None


async def create_parts(s: AsyncSession, document_id: UUID, plans: Sequence[dict[str, Any]]) -> None:
    """Store a document's parts (pending), owned by the document's owner, and their count."""
    await s.execute(text("DELETE FROM document_parts WHERE document_id = :d"), {"d": document_id})
    await s.execute(
        text(
            "INSERT INTO document_parts (owner_user_id, document_id, part_no, start_cursor,"
            " end_offset, end_page) SELECT d.owner_user_id, d.id, :n, CAST(:c AS jsonb), :eo,"
            " :ep FROM documents d WHERE d.id = :d"
        ),
        [
            {
                "d": document_id,
                "n": n,
                "c": _j(plan.get("cursor") or {}),
                "eo": plan.get("end_offset"),
                "ep": plan.get("end_page"),
            }
            for n, plan in enumerate(plans)
        ],
    )
    await s.execute(
        text("UPDATE documents SET parts_total = :n, updated_at = now() WHERE id = :d"),
        {"d": document_id, "n": len(plans)},
    )


async def claim_part(s: AsyncSession, document_id: UUID, owner: str, ttl_s: int, limit: int) -> Any:
    """Take the next part of the document to read: pending, or running under an expired lease.
    ``"busy"`` when ``limit`` parts of this client already run (the part waits); None when no
    part is left to take. The count and the claim are serialised per client."""
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('document_parts:' || app_tenant()::text))")
    )
    running = await s.scalar(
        text(
            "SELECT count(*) FROM document_parts WHERE status = 'running'"
            " AND lease_expires_at > now() AND lease_owner IS DISTINCT FROM :o"
        ),
        {"o": owner},
    )
    nxt = (
        await s.execute(
            text(
                "SELECT part_no FROM document_parts WHERE document_id = :d AND (status ="
                " 'pending' OR (status = 'running' AND lease_expires_at < now()))"
                " ORDER BY part_no LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"d": document_id},
        )
    ).first()
    if nxt is None:
        return None
    if int(running or 0) >= limit:
        return "busy"
    return (
        await s.execute(
            text(
                f"UPDATE document_parts SET status = 'running', lease_owner = :o,"  # nosec B608 -- fixed columns.
                f" lease_expires_at = now() + make_interval(secs => :ttl), attempts = attempts + 1,"
                f" started_at = coalesce(started_at, now()), updated_at = now()"
                f" WHERE document_id = :d AND part_no = :n RETURNING {_PART_COLS}"
            ),
            {"d": document_id, "n": int(nxt.part_no), "o": owner, "ttl": ttl_s},
        )
    ).first()


async def renew_part(
    s: AsyncSession, document_id: UUID, part_no: int, owner: str, ttl_s: int
) -> bool:
    """Extend a part's lease (False: another worker took it, or the document is gone)."""
    res = await s.execute(
        text(
            "UPDATE document_parts SET lease_expires_at = now() + make_interval(secs => :ttl),"
            " updated_at = now() WHERE document_id = :d AND part_no = :n AND lease_owner = :o"
            " AND status = 'running'"
        ),
        {"d": document_id, "n": part_no, "o": owner, "ttl": ttl_s},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


async def renew_document(s: AsyncSession, document_id: UUID, owner: str, ttl_s: int) -> bool:
    """Extend a document's processing lease (False: lost)."""
    res = await s.execute(
        text(
            "UPDATE documents SET lease_expires_at = now() + make_interval(secs => :ttl)"
            " WHERE id = :d AND lease_owner = :o"
        ),
        {"d": document_id, "o": owner, "ttl": ttl_s},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


async def clear_part(s: AsyncSession, document_id: UUID, part_no: int) -> None:
    """Remove what an earlier, interrupted read of this part wrote (it starts clean)."""
    lo, hi = part_no * PART_SPAN, (part_no + 1) * PART_SPAN - 1
    for sql in (
        "DELETE FROM document_redactions WHERE document_id = :d"
        " AND passage_seq BETWEEN :lo AND :hi",
        "DELETE FROM document_passages WHERE document_id = :d AND seq BETWEEN :lo AND :hi",
        "DELETE FROM document_table_rows WHERE document_id = :d AND part_no = :n",
        "DELETE FROM document_members WHERE document_id = :d AND part_no = :n",
    ):
        await s.execute(text(sql), {"d": document_id, "lo": lo, "hi": hi, "n": part_no})


async def alive(s: AsyncSession, document_id: UUID) -> bool:
    """Whether the document still exists (locked against deletion for the transaction)."""
    found = await s.execute(
        text("SELECT 1 FROM documents WHERE id = :d FOR SHARE"), {"d": document_id}
    )
    return found.first() is not None


async def finish_part(
    s: AsyncSession,
    document_id: UUID,
    part_no: int,
    owner: str,
    passages: int,
    evidence: dict[str, Any],
) -> bool:
    """Mark a part read (False: the lease was lost meanwhile)."""
    res = await s.execute(
        text(
            "UPDATE document_parts SET status = 'done', passages = :p, evidence = CAST(:e AS"
            " jsonb), failure = NULL, lease_owner = NULL, lease_expires_at = NULL, finished_at ="
            " now(), updated_at = now() WHERE document_id = :d AND part_no = :n AND lease_owner"
            " = :o AND status = 'running'"
        ),
        {"d": document_id, "n": part_no, "o": owner, "p": passages, "e": _j(evidence)},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


async def fail_part(
    s: AsyncSession, document_id: UUID, part_no: int, owner: str, failure: str
) -> None:
    """Mark a part failed with its reason (what it wrote is removed)."""
    await clear_part(s, document_id, part_no)
    await s.execute(
        text(
            "UPDATE document_parts SET status = 'failed', failure = :f, passages = 0,"
            " lease_owner = NULL, lease_expires_at = NULL, finished_at = now(), updated_at ="
            " now() WHERE document_id = :d AND part_no = :n AND lease_owner = :o"
        ),
        {"d": document_id, "n": part_no, "o": owner, "f": failure[:200]},
    )


async def retry_part(s: AsyncSession, document_id: UUID, part_no: int) -> bool:
    """A failed part goes back to the queue ("Try again"); False when it had not failed."""
    res = await s.execute(
        text(
            "UPDATE document_parts SET status = 'pending', failure = NULL, updated_at = now()"
            " WHERE document_id = :d AND part_no = :n AND status = 'failed'"
        ),
        {"d": document_id, "n": part_no},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


_MEMBER_COLS = (
    "id, member_path, role, declared_type, bytes, status, failure, passages, tables, audience,"
    " part_no, rows_imported, rows_merged, rows_skipped, created_at, updated_at"
)


async def set_member_import_counts(
    s: AsyncSession, document_id: UUID, member_path: str, imported: int, merged: int, skipped: int
) -> None:
    """What the import made of a member's rows (the reconciliation on the Uploaded files sheet)."""
    await s.execute(
        text(
            "UPDATE document_members SET rows_imported = :i, rows_merged = :m, rows_skipped = :k,"
            " updated_at = now() WHERE document_id = :d AND member_path = :p"
        ),
        {"d": document_id, "p": member_path, "i": imported, "m": merged, "k": skipped},
    )


async def record_members(
    s: AsyncSession,
    document_id: UUID,
    account_id: UUID | None,
    part_no: int | None,
    members: Sequence[Mapping[str, Any]],
) -> int:
    """One row per file inside an uploaded zip (``document_members``, 0102), written by the
    part that read, skipped or failed it: an existing row for the path is overwritten (a part
    read again starts clean). Each item: ``member_path``, ``role``, ``declared_type``,
    ``bytes``, ``status`` (read, skipped, failed), ``failure``, ``passages``, ``tables``,
    ``audience``. Returns the rows written."""
    n = 0
    for m in members:
        await s.execute(
            text(
                "INSERT INTO document_members (owner_user_id, document_id, account_id,"
                " member_path, role, declared_type, bytes, status, failure, passages, tables,"
                " audience, part_no) SELECT d.owner_user_id, d.id, :a, :p, :r, :t, :b, :st, :f,"
                " :np, :nt, :au, :pn FROM documents d WHERE d.id = :d"
                " ON CONFLICT (document_id, member_path) DO UPDATE SET role = EXCLUDED.role,"
                " declared_type = EXCLUDED.declared_type, bytes = EXCLUDED.bytes,"
                " status = EXCLUDED.status, failure = EXCLUDED.failure,"
                " passages = EXCLUDED.passages, tables = EXCLUDED.tables,"
                " audience = EXCLUDED.audience, part_no = EXCLUDED.part_no, updated_at = now()"
            ),
            {
                "d": document_id,
                "a": account_id,
                "p": str(m["member_path"])[:1024],
                "r": str(m.get("role") or "other"),
                "t": (str(m["declared_type"])[:40] if m.get("declared_type") else None),
                "b": int(m["bytes"]) if m.get("bytes") is not None else None,
                "st": str(m.get("status") or "read"),
                "f": (str(m["failure"])[:200] if m.get("failure") else None),
                "np": int(m.get("passages") or 0),
                "nt": int(m.get("tables") or 0),
                "au": str(m.get("audience") or "client"),
                "pn": part_no,
            },
        )
        n += 1
    return n


async def members(s: AsyncSession, document_id: UUID) -> list[Any]:
    """A document's member rows in path order (empty for a file that is not an archive)."""
    rows = await s.execute(
        text(
            f"SELECT {_MEMBER_COLS} FROM document_members WHERE document_id = :d"  # nosec B608 -- fixed columns.
            " ORDER BY member_path"
        ),
        {"d": document_id},
    )
    return list(rows.all())


async def member_paths(s: AsyncSession, document_id: UUID) -> set[str]:
    """The member paths a document already has rows for."""
    rows = await s.execute(
        text("SELECT member_path FROM document_members WHERE document_id = :d"),
        {"d": document_id},
    )
    return {str(r.member_path) for r in rows}


async def import_notes(
    s: AsyncSession,
    account_id: UUID,
    run_id: UUID,
    document_id: UUID,
    notes: Sequence[Mapping[str, Any]],
) -> int:
    """Store the notes of an uploaded workspace (``research_notes``, 0105): each item has
    ``kind`` (pull_log, pass1, sense_check, restore, note), ``title``, ``text``, ``audience``
    and ``member_path``; a note the run already holds is left as is. Returns the rows written."""
    written = 0
    for note in notes:
        body = str(note.get("text") or "")[:200000]
        if not body.strip():
            continue
        res = await s.execute(
            text(
                "INSERT INTO research_notes (owner_user_id, account_id, run_id, document_id,"
                " kind, title, text, audience, member_path)"
                " SELECT d.owner_user_id, :a, :r, d.id, :kind, :title, :body, :audience, :member"
                " FROM documents d WHERE d.id = :d"
                " ON CONFLICT (tenant_id, run_id, kind, title) DO NOTHING"
            ),
            {
                "a": account_id,
                "r": run_id,
                "d": document_id,
                "kind": str(note.get("kind") or "note"),
                "title": str(note.get("title") or "note")[:300],
                "body": body,
                "audience": str(note.get("audience") or "client"),
                "member": (str(note.get("member_path") or "")[:1024] or None),
            },
        )
        written += int(bool(res.rowcount))  # type: ignore[attr-defined]
    return written


async def import_pull_calls(
    s: AsyncSession,
    account_id: UUID,
    run_id: UUID,
    document_id: UUID,
    calls: Sequence[Mapping[str, Any]],
) -> int:
    """Store the pull log's call table of an uploaded workspace (``pull_log_calls``, 0105), one
    row per line in order; a position the run already holds is left as is."""
    written = 0
    for position, call in enumerate(calls):
        res = await s.execute(
            text(
                "INSERT INTO pull_log_calls (owner_user_id, account_id, run_id, position, step,"
                " tool_role, parameters, result_count, output_file)"
                " SELECT d.owner_user_id, :a, :r, :p, :step, :tool, :params, :count, :file"
                " FROM documents d WHERE d.id = :d"
                " ON CONFLICT (tenant_id, run_id, position) DO NOTHING"
            ),
            {
                "a": account_id,
                "r": run_id,
                "d": document_id,
                "p": position,
                "step": (str(call.get("step") or "")[:40] or None),
                "tool": (str(call.get("tool") or "")[:200] or None),
                "params": (str(call.get("parameters") or "")[:4000] or None),
                "count": (str(call.get("result") or "")[:1000] or None),
                "file": (str(call.get("output") or "")[:500] or None),
            },
        )
        written += int(bool(res.rowcount))  # type: ignore[attr-defined]
    return written


async def set_audience(s: AsyncSession, document_id: UUID, audience: str) -> bool:
    """Who may see a document: client, owner_only or internal (0102)."""
    res = await s.execute(
        text("UPDATE documents SET audience = :v, updated_at = now() WHERE id = :d"),
        {"d": document_id, "v": audience},
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


async def parts(s: AsyncSession, document_id: UUID) -> list[Any]:
    """A document's parts in order."""
    rows = await s.execute(
        text(
            f"SELECT {_PART_COLS} FROM document_parts WHERE document_id = :d ORDER BY part_no"  # nosec B608 -- fixed columns.
        ),
        {"d": document_id},
    )
    return list(rows.all())


async def part_counts(s: AsyncSession, document_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    """Per document: parts done, running and failed (with their reasons), and passages so far."""
    if not document_ids:
        return {}
    rows = await s.execute(
        text(
            "SELECT document_id, count(*) FILTER (WHERE status = 'done') AS done,"
            " count(*) FILTER (WHERE status = 'running') AS running,"
            " coalesce(sum(passages), 0) AS passages,"
            " coalesce(jsonb_agg(jsonb_build_object('part', part_no, 'failure', failure)"
            " ORDER BY part_no) FILTER (WHERE status = 'failed'), '[]') AS failed,"
            " coalesce(sum((evidence->>'members_read')::int), 0) AS members_read,"
            " coalesce(sum((evidence->>'skipped_total')::int), 0) AS skipped_total,"
            " coalesce(jsonb_agg(evidence->'skipped' ORDER BY part_no)"
            " FILTER (WHERE evidence ? 'skipped'), '[]') AS skipped"
            " FROM document_parts WHERE document_id = ANY(:ids) GROUP BY document_id"
        ),
        {"ids": list(document_ids)},
    )
    return {
        r.document_id: {
            "done": int(r.done),
            "running": int(r.running),
            "passages": int(r.passages),
            "failed": list(r.failed),
            "members_read": int(r.members_read or 0),
            "skipped_total": int(r.skipped_total or 0),
            "skipped": [m for part in (r.skipped or []) for m in (part or [])],
        }
        for r in rows
    }


async def lock(s: AsyncSession, document_id: UUID) -> Any:
    """The document row, locked for the transaction (None: gone)."""
    return await get(s, document_id, lock=True)


async def passages_after(s: AsyncSession, document_id: UUID, after: int, limit: int) -> list[Any]:
    """The next page of a document's passages by number (keyset; never the whole document)."""
    rows = await s.execute(
        text(
            "SELECT seq, kind, location, heading_path, content, content_sha256, token_count,"
            " account_id FROM document_passages WHERE document_id = :d AND seq > :after"
            " ORDER BY seq LIMIT :n"
        ),
        {"d": document_id, "after": after, "n": limit},
    )
    return list(rows.all())


async def index_page(
    s: AsyncSession,
    document_id: UUID,
    account_id: UUID,
    rows: Sequence[Any],
    vectors: Sequence[Sequence[float]],
    model: str,
    semantic: Sequence[Sequence[float]] | None = None,
    semantic_model: str | None = None,
    *,
    owner: str | None = None,
) -> bool:
    """Index one page of passages (their range is replaced, so a page indexed twice is written
    once) and remember how far indexing got. False: the document was deleted meanwhile, or
    (with ``owner``) it is no longer being indexed under that lease, so a call that lost its
    lease writes nothing after a re-parse cleared the passages (audit 2026-11-06).
    ``semantic`` are the retrieval vectors (on-server transformer), one per row, or None.

    The document row is locked (FOR NO KEY UPDATE, not FOR SHARE) before the page is written:
    two workers indexing the
    same document both took the share lock and then both updated the row, a deadlock seen on
    staging 2026-10-15. Now the second waits and rewrites the same page (idempotent)."""
    locked = await s.execute(
        text(
            "SELECT 1 FROM documents WHERE id = :d AND (CAST(:o AS text) IS NULL OR"
            " (lease_owner = :o AND status = 'indexing')) FOR NO KEY UPDATE"
        ),
        {"d": document_id, "o": owner},
    )
    if locked.first() is None:
        return False
    lo, hi = int(rows[0].seq), int(rows[-1].seq)
    await s.execute(
        text(
            "DELETE FROM chunks WHERE tenant_id = (SELECT app_tenant()) AND doc_id = :d"
            " AND passage_seq BETWEEN :lo AND :hi"
        ),
        {"d": document_id, "lo": lo, "hi": hi},
    )
    await s.execute(
        text(
            "INSERT INTO chunks (tenant_id, account_id, doc_id, visibility, content,"
            " embedding, passage_seq, location, heading_path, embedding_model,"
            " semantic_embedding, semantic_model) VALUES"
            " ((SELECT app_tenant()), :a, :d, 'private', :c, CAST(:v AS halfvec), :seq,"
            " CAST(:loc AS jsonb), :h, :m, CAST(:sv AS halfvec), :sm)"
        ),
        [
            {
                "a": account_id,
                "d": document_id,
                "c": row.content,
                "v": literal(vec),
                "seq": row.seq,
                "loc": _j(row.location),
                "h": row.heading_path,
                "m": model,
                "sv": literal(sem) if sem is not None else None,
                "sm": semantic_model if sem is not None else None,
            }
            for row, vec, sem in zip(
                rows, vectors, semantic if semantic is not None else [None] * len(rows), strict=True
            )
        ],
    )
    await s.execute(
        # never backwards: a slower call writing an earlier page must not undo a later one
        text(
            "UPDATE documents SET indexed_through = GREATEST(coalesce(indexed_through, -1),"
            " :hi), updated_at = now() WHERE id = :d"
        ),
        {"d": document_id, "hi": hi},
    )
    return True


async def log_embedding_call(
    s: AsyncSession,
    *,
    document_id: UUID,
    model: str,
    key: str,
    input_tokens: int,
    cost: Decimal,
) -> None:
    """One embedding batch in the file model call log (``research_file_model_calls``, the table
    the earlier research-files path also wrote). The row's owner is the document's owner, read
    here: the table's owner trigger looks for a ``research_files`` parent, which a document never
    has, so without this the row would take whoever is acting (none for a system job)."""
    await s.execute(
        text(
            "INSERT INTO research_file_model_calls (tenant_id, owner_user_id, file_id, task, model,"
            " cache_key, served_from, input_tokens, output_tokens, cost_usd) VALUES"
            " ((SELECT app_tenant()), coalesce((SELECT d.owner_user_id FROM documents d"
            " WHERE d.id = :f), (SELECT app_user())), :f, 'embed_passages', :m, :k, 'model', :i,"
            " 0, :c)"
        ),
        {"f": document_id, "m": model, "k": key, "i": input_tokens, "c": cost},
    )


async def add_embedding_tokens(
    s: AsyncSession, tenant_id: UUID, document_id: UUID, tokens: int
) -> None:
    """Add one embedding batch's tokens to the document's running total (called in the same
    transaction as ``log_embedding_call``, so the total equals the call-log rows)."""
    await s.execute(
        text(
            "UPDATE documents SET embedding_tokens = embedding_tokens + :n"
            " WHERE tenant_id = :t AND id = :d"
        ),
        {"n": tokens, "t": tenant_id, "d": document_id},
    )


async def mark_analysis_run(
    s: AsyncSession, tenant_id: UUID, document_id: UUID, run_id: UUID
) -> None:
    """Record the run of the document's own analysis (step 5); the first one stays."""
    await s.execute(
        text(
            "UPDATE documents SET analysis_run_id = coalesce(analysis_run_id, :r)"
            " WHERE tenant_id = :t AND id = :d"
        ),
        {"r": run_id, "t": tenant_id, "d": document_id},
    )


async def set_analysis_tokens(
    s: AsyncSession, tenant_id: UUID, run_id: UUID, input_tokens: int, output_tokens: int
) -> None:
    """Set (not add) the model tokens of a settled analysis run on the document it analysed
    (``runs.lifecycle.settle_once``); a run that is no document's analysis changes no row."""
    await s.execute(
        text(
            "UPDATE documents SET analysis_input_tokens = :i, analysis_output_tokens = :o"
            " WHERE tenant_id = :t AND analysis_run_id = :r"
        ),
        {"i": input_tokens, "o": output_tokens, "t": tenant_id, "r": run_id},
    )


async def mark_imported(s: AsyncSession, document_id: UUID, at: datetime) -> None:
    """Record that the document's rows were read for the deliverable workbooks
    (``documents.deliverables``), rows found or not; the sweep's backfill skips it from then on."""
    await s.execute(
        text("UPDATE documents SET imported_at = :at WHERE id = :d"), {"at": at, "d": document_id}
    )


async def claim_imports(
    s: AsyncSession, now: datetime, limit: int, *, max_attempts: int = 3, base_s: int = 300
) -> list[Any]:
    """Claim up to ``limit`` indexed documents bound to an account whose rows were never read
    for the deliverable workbooks, by marking them now (``FOR UPDATE SKIP LOCKED``: two sweeps
    running at once never read the same document); their ids and owners. An import that failed
    as a whole waits ``base_s`` x 2^(attempts - 1) before it is tried again, and is not tried
    again after ``max_attempts`` (its import run stays failed; PR3, 2026-11-04)."""
    rows = await s.execute(
        text(
            "UPDATE documents SET imported_at = :at WHERE id IN (SELECT d.id FROM documents d"
            " WHERE d.tenant_id = app_tenant() AND d.status = 'indexed'"
            " AND d.account_id IS NOT NULL AND d.imported_at IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM import_runs r WHERE r.tenant_id = d.tenant_id"
            "   AND r.document_id = d.id AND r.status = 'failed' AND (r.attempts >= :max"
            "   OR r.finished_at > CAST(:at AS timestamptz)"
            "     - make_interval(secs => :base * power(2, r.attempts - 1))))"
            " ORDER BY d.created_at LIMIT :n FOR UPDATE SKIP LOCKED) RETURNING id, owner_user_id"
        ),
        {"at": now, "n": limit, "max": max_attempts, "base": base_s},
    )
    return list(rows.all())


async def unmark_imported(s: AsyncSession, document_id: UUID) -> None:
    """Give a claimed document back (its import failed): the next sweep reads it again."""
    await s.execute(
        text("UPDATE documents SET imported_at = NULL WHERE id = :d"), {"d": document_id}
    )


# ------------------------------------------------------------------ the table store (0077)


async def ensure_table(
    s: AsyncSession, document_id: UUID, account_id: UUID | None, table_name: str, member: str | None
) -> UUID:
    """The id of the document's table record, created on first sight (any part may be first)."""
    row = await s.execute(
        text(
            "INSERT INTO document_tables (owner_user_id, document_id, account_id, table_name,"
            " member) SELECT d.owner_user_id, d.id, :a, :t, :m FROM documents d WHERE d.id = :d"
            " ON CONFLICT (document_id, table_name) DO UPDATE SET updated_at = now()"
            " RETURNING id"
        ),
        {"d": document_id, "a": account_id, "t": table_name[:500], "m": member},
    )
    return UUID(str(row.scalar_one()))


async def write_table_rows(
    s: AsyncSession,
    table_id: UUID,
    document_id: UUID,
    account_id: UUID | None,
    part_no: int,
    rows: Sequence[tuple[int, int, dict[str, Any]]],
) -> None:
    """Store rows ``(row number, passage seq, typed cells)`` of one part; a row number already
    stored for the table is left as it is (a part read again wrote it first)."""
    if not rows:
        return
    await s.execute(
        text(
            "INSERT INTO document_table_rows (owner_user_id, table_id, document_id,"
            " account_id, part_no, row_no, seq, cells) SELECT d.owner_user_id, :t, d.id, :a,"
            " :p, :r, :seq, CAST(:c AS jsonb) FROM documents d WHERE d.id = :d"
            " ON CONFLICT (tenant_id, table_id, row_no) DO NOTHING"
        ),
        [
            {
                "t": table_id,
                "d": document_id,
                "a": account_id,
                "p": part_no,
                "r": row_no,
                "seq": seq,
                "c": _j(cells),
            }
            for row_no, seq, cells in rows
        ],
    )


async def set_table_schema(s: AsyncSession, table_id: UUID, record: dict[str, Any]) -> None:
    """Record what a part learned about the table's columns, merged with what other parts
    recorded (type tallies add up, payload stays payload, the first business key stands); the
    row count is counted from the stored rows, so it is exact whatever the order of parts."""
    held = (
        await s.execute(
            text(
                "SELECT columns, business_key FROM document_tables WHERE id = :t FOR NO KEY UPDATE"
            ),
            {"t": table_id},
        )
    ).first()
    if held is None:
        return
    merged: dict[str, dict[str, Any]] = {}
    for column in [*(held.columns or []), *record.get("columns", [])]:
        name = str(column.get("name"))
        into = merged.setdefault(
            name,
            {"name": name, "raw": column.get("raw", name), "payload": False, "dates": 0,
             "numbers": 0, "texts": 0, "lists": 0, "values": set()},
        )  # fmt: skip
        into["payload"] = bool(into["payload"] or column.get("payload"))
        into["values"] |= set(column.get("values") or ())
        for kind in ("dates", "numbers", "texts", "lists"):
            into[kind] += int(column.get(kind) or 0)
    for column in merged.values():
        dates, numbers, texts = column["dates"], column["numbers"], column["texts"]
        column["type"] = (
            "date"
            if dates and not numbers and not texts
            else "number"
            if numbers and not dates and not texts
            else "text"
        )
        # multi-valued: a fifth or more of the text cells hold a list of short items
        column["multi_valued"] = bool(texts and column["lists"] * LIST_TALLY_REQUIRED >= texts)
        # the router's lexicon, from the column as merged: its natural forms, and the value
        # index when the column stayed low-cardinality (the ingest's cap applies here too)
        column["aliases"] = column_aliases(
            str(column["raw"]), str(column["name"]), str(column["type"])
        )
        column["values"] = (
            sorted(column["values"], key=str.casefold)
            if column["type"] == "text"
            and not column["payload"]
            and 0 < len(column["values"]) <= VALUE_INDEX_LIMIT
            else []
        )
    columns = list(merged.values())
    await s.execute(
        text(
            "UPDATE document_tables SET columns = CAST(:c AS jsonb), payload_columns ="
            " CAST(:p AS jsonb), business_key = coalesce(business_key, :k), row_count ="
            " (SELECT count(*) FROM document_table_rows r WHERE r.table_id = :t),"
            " updated_at = now() WHERE id = :t"
        ),
        {
            "t": table_id,
            "c": _j(columns),
            "p": _j([c["name"] for c in columns if c["payload"]]),
            "k": record.get("business_key"),
        },
    )


async def tables_for(s: AsyncSession, accounts: Sequence[UUID]) -> list[Any]:
    """The ingested tables of these accounts (newest document first, then by name), with the
    document's name. A table written before its document was bound (bind_account before
    2026-11-04 left it unbound) counts under the document's account."""
    if not accounts:
        return []
    rows = await s.execute(
        text(
            "SELECT t.id, t.document_id, coalesce(t.account_id, d.account_id) AS account_id,"
            " t.member, t.table_name, t.columns,"
            " t.payload_columns, t.business_key, t.row_count, coalesce(d.name, 'an uploaded"
            " file') AS document, d.created_at FROM document_tables t JOIN documents d ON d.id ="
            " t.document_id WHERE coalesce(t.account_id, d.account_id) = ANY(:scope)"
            # an upload held for its account (it names another company) answers nothing yet
            " AND t.row_count > 0 AND d.status <> 'needs_account'"
            " ORDER BY d.created_at DESC, d.id, t.table_name"
        ),
        {"scope": list(accounts)},
    )
    return list(rows.all())


# the research tables (``view_rows``) read with the row shape of ``document_table_rows``:
# (row_no, seq, cells). The row number counts the table's rows in the order of (account, file
# row) — the file row itself for one account — so "row 17" means the same thing in a query, in
# ``read_rows`` and in the answer; the delivered file's own row number rides in the ``file_row``
# cell, and ``account`` names the account.
_VIEW_ROWS = (
    "(SELECT row_number() OVER (ORDER BY v.account_id, v.position) AS row_no, 0 AS seq,"
    " v.cells || jsonb_build_object('account', coalesce(a.name, a.domain),"
    " 'file_row', v.position) AS cells FROM view_rows v JOIN accounts a ON a.id = v.account_id"
    " WHERE v.run_id = ANY(:runs) AND v.table_kind = :kind AND v.account_id = ANY(:accts)) r"
)


def _newest(table: str, alias: str, same: str) -> str:
    """The account's CURRENT row of each thing the research records per run (the rule of
    ``account_intelligence.replica.current.newest_only``, leakage review 2026-10-30, F2): a
    research that resumes or repeats an account records its people and postings again under a
    new run, and an import adds its rows under the file-import run, so of the rows that are the
    same thing only the newest is read."""
    return (
        f" AND NOT EXISTS (SELECT 1 FROM {table} n WHERE n.tenant_id = {alias}.tenant_id"  # nosec B608 -- fixed SQL fragments; values are bound.
        f" AND n.account_id = {alias}.account_id AND {same}"
        f" AND (n.created_at, n.id) > ({alias}.created_at, {alias}.id))"
    )


# The accounts' current rows in their native tables, read with the same row shape (the
# ``native`` source of ``documents.tables.ViewSource``): an uploaded workspace is imported into
# ``people``, ``job_postings`` and ``tech_detections`` under a file-import run that no export
# reader materialises (``views.materialize`` reads a run's pipeline records, which an import has
# none of; UCB 2026-11-04: Ask had no count tools over 1,600 imported people). Each fragment is
# fixed text whose values are all bound (``:accts``, and ``:hidden`` where the tenant's current
# exclusions apply); the row number counts the table's rows in (account, stored order), the
# ``file_row`` cell the row's place within its account, ``account`` names the account. Dates are
# ISO text (blank when unknown), as the research tables print them. Besides row level security,
# each fragment carries the session tenant (``app_tenant()``) as its own predicate, so a read
# by a role RLS never applies to still sees one tenant (upload hardening PR1, 2026-11-04).
_NATIVE_ROWS: dict[str, str] = {
    "people": (
        "(SELECT row_number() OVER (ORDER BY p.account_id, p.created_at, p.id) AS row_no,"  # nosec B608 -- fixed SQL fragments; values are bound.
        " 0 AS seq, jsonb_build_object('account', coalesce(a.name, a.domain),"
        " 'file_row', row_number() OVER (PARTITION BY p.account_id ORDER BY p.created_at, p.id),"
        " 'name', coalesce(p.name, ''), 'title', coalesce(p.title, ''),"
        " 'level', coalesce(p.job_level, ''), 'location', coalesce(p.location, ''),"
        " 'lanes', coalesce(array_to_string(p.lanes, ', '), ''),"
        " 'priority', coalesce(p.priority, ''), 'scope', coalesce(p.scope_status, ''),"
        " 'origin', coalesce(p.origin, ''), 'profile_url', coalesce(p.profile_url, ''),"
        " 'former_employers', coalesce((SELECT array_to_string(pp.prior_employers, ', ')"
        "   FROM person_profiles pp WHERE pp.tenant_id = p.tenant_id AND pp.run_id = p.run_id"
        "   AND pp.person_key = p.person_key AND pp.erased_at IS NULL LIMIT 1), ''),"
        " 'headline', coalesce(p.headline, ''), 'note', coalesce(p.note, '')) AS cells"
        " FROM people p JOIN accounts a ON a.tenant_id = p.tenant_id AND a.id = p.account_id"
        " WHERE p.tenant_id = app_tenant() AND p.account_id = ANY(:accts) AND NOT p.excluded"
        " AND p.merged_into IS NULL"
        " AND p.erased_at IS NULL AND NOT coalesce(p.internal_only, false)"
        " AND CAST(p.id AS text) <> ALL(CAST(:hidden AS text[]))"
        + _newest("people", "p", "p.person_key IS NOT NULL AND n.person_key = p.person_key")
        + ") r"
    ),
    "jobs": (
        "(SELECT row_number() OVER (ORDER BY j.account_id, j.created_at, j.id) AS row_no,"  # nosec B608 -- fixed SQL fragments; values are bound.
        " 0 AS seq, jsonb_build_object('account', coalesce(a.name, a.domain),"
        " 'file_row', row_number() OVER (PARTITION BY j.account_id ORDER BY j.created_at, j.id),"
        " 'requisition', CASE WHEN left(j.posting_key, 3) = 'id:' THEN substr(j.posting_key, 4)"
        "   ELSE '' END,"
        " 'title', coalesce(j.title, ''), 'status', initcap(j.status),"
        " 'location', coalesce(j.location, ''),"
        " 'platforms', coalesce(array_to_string(j.platforms, ', '), ''),"
        " 'lanes', coalesce(array_to_string(j.lanes, ', '), ''),"
        " 'class', coalesce(j.posting_class, ''), 'seniority', coalesce(j.seniority, ''),"
        " 'posted', coalesce(to_char(j.posted_at, 'YYYY-MM-DD'), ''),"
        " 'first_seen', coalesce(to_char(j.first_seen, 'YYYY-MM-DD'), ''),"
        " 'last_seen', coalesce(to_char(j.last_seen, 'YYYY-MM-DD'), ''),"
        " 'entity', coalesce(j.entity_tag, ''), 'url', coalesce(j.url, ''),"
        " 'summary', coalesce(j.summary_read, '')) AS cells"
        " FROM job_postings j JOIN accounts a ON a.tenant_id = j.tenant_id AND a.id = j.account_id"
        " WHERE j.tenant_id = app_tenant() AND j.account_id = ANY(:accts)"
        " AND NOT coalesce(j.hostile, false)"
        " AND CAST(j.id AS text) <> ALL(CAST(:hidden AS text[]))"
        + _newest("job_postings", "j", "n.posting_key = j.posting_key")
        + ") r"
    ),
    "tech": (
        "(SELECT row_number() OVER (ORDER BY t.account_id, t.created_at, t.id) AS row_no,"  # nosec B608 -- fixed SQL fragments; values are bound.
        " 0 AS seq, jsonb_build_object('account', coalesce(a.name, a.domain),"
        " 'file_row', row_number() OVER (PARTITION BY t.account_id ORDER BY t.created_at, t.id),"
        " 'technology', t.technology, 'sources', coalesce(t.source_count, 0),"
        " 'cross_check', coalesce(t.cross_check_result, ''),"
        " 'first_seen', coalesce(t.first_seen_at, ''), 'last_seen', coalesce(t.last_seen_at, ''))"
        " AS cells"
        " FROM tech_detections t JOIN accounts a ON a.tenant_id = t.tenant_id"
        " AND a.id = t.account_id"
        " WHERE t.tenant_id = app_tenant() AND t.account_id = ANY(:accts)"
        + _newest("tech_detections", "t", "n.technology = t.technology")
        + ") r"
    ),
}
NATIVE_KINDS = tuple(_NATIVE_ROWS)


def rows_source(view: Any) -> tuple[str, dict[str, Any]]:
    """The row source of a research table (``documents.tables.ViewSource``) as the fixed SQL
    fragment aliased ``r`` (``row_no``, ``seq``, ``cells``) and the parameters it binds."""
    if getattr(view, "source", "view_rows") == "native":
        sql = _NATIVE_ROWS[view.kind]
        params: dict[str, Any] = {"accts": list(view.account_ids)}
        if ":hidden" in sql:
            params["hidden"] = [str(h) for h in getattr(view, "hidden", ())]
        return sql, params
    return _VIEW_ROWS, {
        "runs": list(view.run_ids),
        "kind": view.kind,
        "accts": list(view.account_ids),
    }


async def table_rows(
    s: AsyncSession,
    table_id: UUID,
    where: str,
    params: dict[str, Any],
    limit: int,
    *,
    view: Any | None = None,
) -> list[Any]:
    """Rows ``(row_no, seq, cells)`` of one table matching ``where`` (a fragment over the row
    alias ``r`` whose values are all bound in ``params``; ``documents.tables`` builds it from the
    column whitelist and never from the caller's text), in row order, at most ``limit``. ``view``
    (a ``documents.tables.ViewSource``) reads a research table from ``view_rows`` instead."""
    if view is not None:
        source, bound = rows_source(view)
        found = await s.execute(
            text(
                "SELECT r.row_no, r.seq, r.cells FROM "  # nosec B608
                + source  # fixed text; the fragment binds every value (see above)
                + (f" WHERE ({where})" if where else "")
                + " ORDER BY r.row_no LIMIT :n"
            ),
            {**params, **bound, "n": limit},
        )
        return list(found.all())
    rows = await s.execute(
        text(
            # the fragment names whitelisted columns as bound jsonb keys and binds every value
            # (documents.tables._sql); no caller text is in it
            "SELECT r.row_no, r.seq, r.cells FROM document_table_rows r WHERE r.table_id = :t"  # nosec B608
            + (f" AND ({where})" if where else "")
            + " ORDER BY r.row_no LIMIT :n"
        ),
        {**params, "t": table_id, "n": limit},
    )
    return list(rows.all())


async def table_chars(s: AsyncSession, table_id: UUID, *, view: Any | None = None) -> int:
    """The characters the table's rows hold (cells as JSON): what ``replica.read_plan`` weighs
    against the budget before it puts a whole table in front of the model."""
    if view is not None:
        source, bound = rows_source(view)
        found = await s.scalar(
            text("SELECT coalesce(sum(length(r.cells::text)), 0) FROM " + source),  # nosec B608
            bound,
        )
    else:
        found = await s.scalar(
            text(
                "SELECT coalesce(sum(length(cells::text)), 0) FROM document_table_rows"
                " WHERE table_id = :t"
            ),
            {"t": table_id},
        )
    return int(found or 0)


async def table_rows_by_no(
    s: AsyncSession, table_id: UUID, row_nos: Sequence[int], *, view: Any | None = None
) -> list[Any]:
    """Rows ``(row_no, cells)`` of one table by row number (``read_rows``: the payload cells a
    query leaves out), in row order. The numbers are bound as one array parameter."""
    numbers = [int(n) for n in row_nos]
    if view is not None:
        source, bound = rows_source(view)
        found = await s.execute(
            text(
                "SELECT r.row_no, r.cells FROM "
                + source  # nosec B608
                + " WHERE r.row_no = ANY(:rows) ORDER BY r.row_no"
            ),
            {**bound, "rows": numbers},
        )
        return list(found.all())
    rows = await s.execute(
        text(
            "SELECT r.row_no, r.cells FROM document_table_rows r WHERE r.table_id = :t"
            " AND r.row_no = ANY(:rows) ORDER BY r.row_no"
        ),
        {"t": table_id, "rows": numbers},
    )
    return list(rows.all())


async def indexed_documents(s: AsyncSession, limit: int) -> list[Any]:
    """Every indexed document of this client, oldest first: the ones ``reindex`` sends through
    the current read again so passages, table store and index are rebuilt."""
    rows = await s.execute(
        text(
            "SELECT d.id, d.owner_user_id FROM documents d WHERE d.status = 'indexed'"
            " ORDER BY d.created_at LIMIT :n"
        ),
        {"n": limit},
    )
    return list(rows.all())


async def documents_without_tables(s: AsyncSession, limit: int) -> list[Any]:
    """Indexed documents holding table rows (crm_row passages) but no table record: the ones
    ingested before the table store existed (``reingest-tables``)."""
    rows = await s.execute(
        text(
            "SELECT d.id, d.owner_user_id, d.account_id FROM documents d WHERE d.status ="
            " 'indexed' AND EXISTS (SELECT 1 FROM document_passages p WHERE p.document_id = d.id"
            " AND p.kind = 'crm_row') AND NOT EXISTS (SELECT 1 FROM document_tables t WHERE"
            " t.document_id = d.id) ORDER BY d.created_at LIMIT :n"
        ),
        {"n": limit},
    )
    return list(rows.all())


async def tables_held(s: AsyncSession) -> tuple[int, int]:
    """The tables and table rows this tenant's store holds right now (the reingest report's
    per-workspace numbers)."""
    row = (
        await s.execute(
            text(
                "SELECT (SELECT count(*) FROM document_tables),"
                " (SELECT count(*) FROM document_table_rows)"
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def reset_for_reparse(s: AsyncSession, document_id: UUID) -> bool:
    """Send an indexed document through its read again: its parts go back to pending, its
    passages, index and tables are cleared and its status is ``parsing``; False when it is
    not indexed (already under way, or never read)."""
    res = await s.execute(
        text(
            "UPDATE documents SET status = 'parsing', indexed_through = NULL, imported_at = NULL,"
            " stop_detail = NULL, updated_at = now() WHERE id = :d AND status = 'indexed'"
        ),
        {"d": document_id},
    )
    if not res.rowcount:  # type: ignore[attr-defined]
        return False
    await clear_passages(s, document_id)
    await s.execute(
        text(
            "UPDATE document_parts SET status = 'pending', failure = NULL, passages = 0,"
            " evidence = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = now()"
            " WHERE document_id = :d"
        ),
        {"d": document_id},
    )
    return True


async def raw_file(s: AsyncSession, sha256: str) -> Any | None:
    """The raw-file registry row of a hash (None when these bytes were never stored): the raw
    file store (0078) keeps one object per (tenant, sha256)."""
    return (
        await s.execute(
            text("SELECT sha256, object_key, size_bytes FROM raw_files WHERE sha256 = :h"),
            {"h": sha256},
        )
    ).first()


async def register_raw_file(
    s: AsyncSession, sha256: str, size_bytes: int, object_key: str, document_id: UUID
) -> None:
    """Register an object's hash, owned by the document's owner (copied like 0077's writers; a
    re-upload of the same bytes registers nothing)."""
    await s.execute(
        text(
            "INSERT INTO raw_files (sha256, size_bytes, object_key, first_document_id,"
            " owner_user_id) SELECT :h, :b, :k, :d, d.owner_user_id FROM documents d"
            " WHERE d.id = :d ON CONFLICT (tenant_id, sha256) DO NOTHING"
        ),
        {"h": sha256, "b": size_bytes, "k": object_key, "d": document_id},
    )


async def documents_with_sha(s: AsyncSession, sha256: str) -> int:
    """How many surviving documents of the tenant share a hash (the raw object stays while
    any do)."""
    return int(
        await s.scalar(text("SELECT count(*) FROM documents WHERE sha256 = :h"), {"h": sha256}) or 0
    )


async def remove_raw_file(s: AsyncSession, sha256: str) -> None:
    """Drop the registry row of a hash (the caller deletes the object; 0078)."""
    await s.execute(text("DELETE FROM raw_files WHERE sha256 = :h"), {"h": sha256})


async def any_not_indexed(
    s: AsyncSession, account_id: UUID | None, statuses: Sequence[str]
) -> bool:
    """Whether a bound document of the account (of every account the caller sees, without one)
    is still in one of ``statuses`` (not yet indexed: Ask says its search may miss it)."""
    found = await s.scalar(
        text(
            "SELECT 1 FROM documents WHERE tenant_id = (SELECT app_tenant())"
            " AND account_id IS NOT NULL AND status = ANY(:st)"
            " AND (CAST(:a AS uuid) IS NULL OR account_id = :a) LIMIT 1"
        ),
        {"a": account_id, "st": list(statuses)},
    )
    return found is not None
