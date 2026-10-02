"""src/db/session.py - tenant-scoped database access. All tenant queries go through tenant_txn()."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# DATABASE_URL = postgresql+asyncpg://app_rw:***@aurora-writer/ai (role is NOBYPASSRLS, not owner)
engine = create_async_engine(
    os.environ["DATABASE_URL"],
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    pool_pre_ping=True,
    connect_args={"server_settings": {"application_name": "ai-api"}},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


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
            make_sync_url(os.environ["DATABASE_URL"]),
            pool_pre_ping=True,
        )
    return _sync_engine


@asynccontextmanager
async def tenant_txn(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """One transaction == one tenant context.

    set_config(..., true) is transaction-local: when the transaction ends, the setting is reset,
    so a pooled connection can never carry tenant A into tenant B's request. (A bare SET LOCAL
    outside an explicit transaction is a no-op, which is why v1's middleware was unsafe.)
    """
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        yield session
