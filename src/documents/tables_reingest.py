"""``reingest-tables``: give the documents ingested before the table store (migration 0077)
their tables, by reading them again.

The store is written while a document is read, so a workbook uploaded before it existed has
crm_row passages but no table record, and a tabular question about it still answers from
passages. This pass finds those documents in every client (indexed, holding crm_row passages,
no ``document_tables`` row), resets them to ``parsing`` with every part pending (their passages,
index rows and redaction map are cleared first, so nothing is stored twice) and queues them for
the worker, which reads, chunks, stores and indexes them exactly as a new upload. Idempotent: a
document with a table record, or not ``indexed`` (already under way, failed, never bound), is
skipped, and a second run queues nothing more — a re-run after the worker has finished then
reports the rebuilt tables and rows per client (``workspaces`` in the result, one
``documents.reingest_tables.workspace`` log line each: the tenant, the documents queued, the
tables and rows held). With ``workspace`` only that client's documents are looked at. ``limit``
documents per client per run.

GUARANTEE(test_reingest_tables_queues_pre_store_documents_once)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from sqlalchemy import text

from documents import repo
from obs.logging import logger

_log = logger(__name__)


async def reingest_tables(
    sessions: Callable[[], AbstractAsyncContextManager[Any]],
    txn: Callable[..., AbstractAsyncContextManager[Any]],
    scheduler: Any,
    *,
    workspace: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """See the module docstring. Returns how many documents were queued and skipped, and how
    many clients were looked at, with the per-client report under ``workspaces``."""
    if workspace is not None:
        tenants = [workspace]
    else:
        async with sessions() as plain:
            listed = await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
            tenants = [UUID(str(r.tenant_id)) for r in listed]
    queued = skipped = 0
    workspaces: list[dict[str, Any]] = []
    # LOOP(reingest clients): cap=every client once per run, `limit` documents each; exit=last
    # client
    for tenant in tenants:
        async with txn(tenant, system=True) as s:
            rows = await repo.documents_without_tables(s, limit)
            held = await repo.tables_held(s)
        before = queued
        # LOOP(reingest documents): cap=`limit` per client per run; exit=rows exhausted
        for row in rows:
            document_id = UUID(str(row.id))
            async with txn(tenant, user=str(row.owner_user_id), system=True) as s:
                reset = await repo.reset_for_reparse(s, document_id)
            if not reset:
                skipped += 1
                continue
            await scheduler.enqueue_document(tenant, document_id)
            queued += 1
        _log.info(
            "documents.reingest_tables.workspace",
            extra={
                "tenant_id": str(tenant),
                "count": queued - before,
                "tables_held": held[0],
                "rows_held": held[1],
            },
        )
        workspaces.append(
            {
                "tenant": str(tenant),
                "queued": queued - before,
                "tables_held": held[0],
                "rows_held": held[1],
            }
        )
    _log.info(
        "documents.reingest_tables",
        extra={"count": queued, "dropped": skipped, "items_read": len(tenants)},
    )
    return {"queued": queued, "skipped": skipped, "clients": len(tenants), "workspaces": workspaces}
