"""LangGraph Postgres checkpointer on a dedicated, tenant-bound connection.

The checkpoint tables are library-owned; migration 0002 puts a tenant-prefix RLS policy on them
(``thread_id = '<tenant>:<run>'``). The connection is private to one slice: the tenant setting is
session-scoped (autocommit has no long transaction) and the connection is closed when the slice
ends, so it never returns to a pool carrying a tenant. ``setup()`` runs once in the migrator,
never here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row


def checkpointer_factory(dsn: str):  # type: ignore[no-untyped-def]
    """``(tenant_id) -> async context manager yielding an AsyncPostgresSaver``."""

    @asynccontextmanager
    async def open_saver(tenant_id: UUID) -> AsyncIterator[AsyncPostgresSaver]:
        """A saver on a fresh connection bound to the tenant, closed when the slice ends."""
        conn = await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row, prepare_threshold=0
        )
        try:
            await conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            yield AsyncPostgresSaver(conn)
        finally:
            await conn.close()

    return open_saver
