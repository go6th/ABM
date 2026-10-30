"""One consistent snapshot per build (B1).

``open_snapshot`` is ``tenant_txn(tenant_id, snapshot=True)``: a ``REPEATABLE READ, READ ONLY``
transaction under the tenant context. Every reader, including the people and jobs repositories it
reuses, reads through ``session_factory(session)``, which hands the SAME session to any code that
asks a ``TxnFactory`` for a transaction, so nothing reads outside the snapshot. A row committed by
a concurrent writer after the first query is invisible to the whole build (tested).
GUARANTEE(test_one_snapshot_under_a_concurrent_writer)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.db.session import tenant_txn

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def open_snapshot(tenant_id: UUID) -> AbstractAsyncContextManager[AsyncSession]:
    """The snapshot transaction (see the module docstring)."""
    return tenant_txn(tenant_id, snapshot=True)


def open_system_lookup(tenant_id: UUID) -> AbstractAsyncContextManager[AsyncSession]:
    """A read-only transaction in SYSTEM scope, for the one lookup of a build that must see every
    account of the tenant whoever asked for it: the other accounts' names, which are forbidden
    terms (``readers.pg``). Nothing else is read through it."""
    return tenant_txn(tenant_id, snapshot=True, system=True)


def session_factory(session: AsyncSession, tenant_id: UUID) -> TxnFactory:
    """A ``TxnFactory`` that yields ``session`` (inside the snapshot) for ``tenant_id`` only."""

    @asynccontextmanager
    async def factory(tid: UUID) -> AsyncIterator[AsyncSession]:
        """The snapshot session, for the snapshot's tenant only."""
        if tid != tenant_id:
            raise PermissionError("a snapshot serves one tenant")
        yield session

    return factory
