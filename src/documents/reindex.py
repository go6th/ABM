"""``reindex``: give a client's indexed documents the current read again, rebuilding their
passages, table store and index rows.

Ingestion changes over time (new shapes, new passage metadata, a new table store); the only
truth is the raw file, so a rebuild reads every indexed document from its stored bytes exactly
as a new upload — ``reset_for_reparse`` clears its passages, index and tables and returns its
parts to pending, then the worker's normal read chunks, stores and indexes them. With
``workspace`` only that client's documents are queued; without it, every client's (like
``reingest-tables``). Idempotent in the same way: a document already reading, failed or never
bound is skipped by ``reset_for_reparse``, ``limit`` documents per client per run.

GUARANTEE(test_reindex_requeues_the_workspace_indexed_documents_only)
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


async def reindex_documents(
    sessions: Callable[[], AbstractAsyncContextManager[Any]],
    txn: Callable[..., AbstractAsyncContextManager[Any]],
    scheduler: Any,
    *,
    workspace: UUID | None = None,
    limit: int = 200,
) -> dict[str, int]:
    """See the module docstring. Returns how many documents were queued and skipped, and how
    many clients were looked at."""
    if workspace is not None:
        tenants = [workspace]
    else:
        async with sessions() as plain:
            listed = await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
            tenants = [UUID(str(r.tenant_id)) for r in listed]
    queued = skipped = 0
    # LOOP(reindex clients): cap=the workspaces in scope once per run, `limit` documents each;
    # exit=last client
    for tenant in tenants:
        async with txn(tenant, system=True) as s:
            rows = await repo.indexed_documents(s, limit)
        # LOOP(reindex documents): cap=`limit` per client per run; exit=rows exhausted
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
        "documents.reindex",
        extra={"count": queued, "dropped": skipped, "items_read": len(tenants)},
    )
    return {"queued": queued, "skipped": skipped, "clients": len(tenants)}
