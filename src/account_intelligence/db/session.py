"""Tenant-scoped database access. All tenant queries go through tenant_txn()."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from account_intelligence.config import get_settings

_settings = get_settings()
engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args={"server_settings": {"application_name": "ai-api"}},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
_BOUND: list[async_sessionmaker[AsyncSession]] = []


def bind_engine(new_engine: AsyncEngine) -> None:
    """Use ``new_engine`` for every ``tenant_txn`` (``app.container`` calls this once at startup
    with the Cloud SQL connector engine in staging and production)."""
    _BOUND.clear()
    _BOUND.append(async_sessionmaker(new_engine, expire_on_commit=False))


def _sessions() -> async_sessionmaker[AsyncSession]:
    return _BOUND[0] if _BOUND else SessionLocal


def make_sync_url(url: str) -> str:
    """Derive a psycopg URL from the async DATABASE_URL so one env var drives both engines."""
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgresql+asyncpg"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


_sync_engine: Engine | None = None


def get_sync_engine() -> Engine:
    """Sync engine for tools and tests (migrations use the migrator URL, not this)."""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            make_sync_url(get_settings().database_url),
            pool_pre_ping=True,
        )
    return _sync_engine


@asynccontextmanager
async def tenant_txn(tenant_id: UUID, *, snapshot: bool = False) -> AsyncIterator[AsyncSession]:
    """One transaction == one tenant context.

    set_config(..., true) is transaction-local: when the transaction ends, the setting is reset,
    so a pooled connection can never carry tenant A into tenant B's request. (A bare SET LOCAL
    outside an explicit transaction is a no-op, which is why v1's middleware was unsafe.)

    ``snapshot=True`` makes the transaction ``REPEATABLE READ, READ ONLY`` (issued before any
    query, as PostgreSQL requires): every read sees one consistent snapshot and nothing can be
    written (the export builders' B1).
    """
    async with _sessions()() as session, session.begin():
        if snapshot:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        yield session
