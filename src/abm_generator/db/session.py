"""Tenant-scoped database access.

Invariants enforced here (I1, I3):
- Every tenant query runs inside a transaction where ``app.tenant_id`` is set with
  transaction-local scope via ``set_config(..., is_local=true)``. When the transaction ends
  the setting resets, so a pooled connection can never carry tenant A's context into
  tenant B's request.
- The runtime role must be non-owner and NOBYPASSRLS; ``check_app_role_is_safe`` verifies
  this at startup and in tests (fail-closed).

A bare ``SET LOCAL`` outside an explicit transaction is a no-op — which is why the context
is always set inside ``session.begin()``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql.elements import TextClause

from abm_generator.config import get_settings

_T = TypeVar("_T")

TENANT_SETTING = "app.tenant_id"


def set_tenant_stmt(tenant_id: UUID) -> TextClause:
    """SQL statement that sets transaction-local tenant context (for tests/introspection)."""
    return text("SELECT set_config(:setting, :tid, true)").bindparams(
        setting=TENANT_SETTING,
        tid=str(tenant_id),
    )


_ENGINE: AsyncEngine | None = None
_SESSIONMAKER: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (transaction-pooling compatible)."""
    global _ENGINE
    if _ENGINE is None:
        settings = get_settings()
        _ENGINE = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "application_name": "abm-generator",
                    "statement_timeout": str(settings.db_statement_timeout_ms),
                }
            },
        )
    return _ENGINE


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        _SESSIONMAKER = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _SESSIONMAKER


async def dispose_engine() -> None:
    """Dispose the engine (called on shutdown / between test modules)."""
    global _ENGINE, _SESSIONMAKER
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _SESSIONMAKER = None


@asynccontextmanager
async def tenant_txn(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """One transaction == one tenant context (I1).

    Yields a session inside an explicit transaction with transaction-local
    ``app.tenant_id``. On exit (commit or rollback) the setting is reset on the pooled
    connection, so no cross-tenant leakage is possible through the pool.
    """
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text("SELECT set_config(:setting, :tid, true)"),
            {"setting": TENANT_SETTING, "tid": str(tenant_id)},
        )
        yield session


async def run_in_tenant(tenant_id: UUID, fn: Callable[[AsyncSession], Awaitable[_T]]) -> _T:
    """Run ``fn`` inside one tenant transaction and return its result."""
    async with tenant_txn(tenant_id) as session:
        return await fn(session)


@asynccontextmanager
async def unset_txn() -> AsyncIterator[AsyncSession]:
    """A session with NO tenant context (fail-closed tests use this)."""
    async with get_sessionmaker()() as session, session.begin():
        yield session


async def current_tenant_setting(session: AsyncSession) -> str | None:
    """Read the current ``app.tenant_id`` setting (None or '' when unset)."""
    row: str | None = (
        await session.execute(
            text("SELECT current_setting(:setting, true)"),
            {"setting": TENANT_SETTING},
        )
    ).scalar_one()
    return row if row not in (None, "") else None


async def check_app_role_is_safe(session: AsyncSession) -> dict[str, object]:
    """Verify the runtime role cannot bypass RLS and does not own tenant tables (I3).

    Returns a report dict; raises ``RuntimeError`` on any violation (fail-closed).
    Intended to be called at service startup and from the isolation suite.
    """
    role_row: str = (await session.execute(text("SELECT current_user"))).scalar_one()
    report: dict[str, object] = {"current_user": role_row}

    bypass_row: bool = (
        await session.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).scalar_one()
    report["rolbypassrls"] = bypass_row
    if bypass_row is True:
        msg = f"runtime role {role_row} has BYPASSRLS; refusing to start (I3)"
        raise RuntimeError(msg)

    super_row: bool = (
        await session.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
    ).scalar_one()
    report["rolsuper"] = super_row
    if super_row is True:
        msg = f"runtime role {role_row} is superuser; refusing to start (I3)"
        raise RuntimeError(msg)

    owner_rows_result = await session.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
            """
        )
    )
    owner_rows: list[str] = list(owner_rows_result.scalars().all())
    report["tables_owned"] = owner_rows
    if owner_rows:
        msg = f"runtime role {role_row} owns tables {owner_rows}; refusing to start (I3)"
        raise RuntimeError(msg)

    return report
