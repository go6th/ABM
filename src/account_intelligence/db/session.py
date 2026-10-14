"""Tenant-scoped database access. All tenant queries go through tenant_txn().

Every transaction carries the tenant and, since migration 0063 (private workspaces), the acting
user: a row of an owned table (``config/ownership.yaml``) is visible only to its own user, or to
a system job. Who is acting comes from a context variable:

* ``set_actor(user)``: the verified caller of a tenant route (``api.guard.authorize``), or the
  owner of the run, document or job a worker task works on;
* ``system_scope()``: scheduler ticks, the sweeper, the isolation check, operator counts. A
  system transaction sees every row of its tenant; rows it inserts into an owned table must name
  their owner (the column default is the acting user).

With neither, owned rows are invisible and inserts into them fail (fail closed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from typing import Any
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
    _settings.runtime_database_url,
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


def session_factory() -> async_sessionmaker[AsyncSession]:
    """The session factory every untenanted job uses: the bound Cloud SQL engine in staging and
    production, the local engine otherwise (never ``SessionLocal`` directly: in the cloud it
    points at the unbound placeholder URL)."""
    return _sessions()


async def bind_cloud_engine(settings: Any) -> Any:
    """In staging and production, open the Cloud SQL connector engine (IAM auth, private IP, the
    registry's pool bounds) and bind it; returns the connector to close at shutdown, or None in
    the local profiles. The API lifespan and the worker command line both start with it."""
    if settings.app_env not in ("staging", "production"):
        return None
    from account_intelligence.platform.gcp.adapters import cloud_sql_engine

    if not (settings.cloud_sql_instance and settings.db_iam_user and settings.db_name):
        raise RuntimeError("CLOUD_SQL_INSTANCE, DB_IAM_USER and DB_NAME are required")
    new_engine, connector = await cloud_sql_engine(
        settings.cloud_sql_instance,
        settings.db_iam_user,
        settings.db_name,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    bind_engine(new_engine)
    return connector


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
            make_sync_url(get_settings().runtime_database_url),
            pool_pre_ping=True,
        )
    return _sync_engine


_ACTOR: ContextVar[str | None] = ContextVar("app_user", default=None)
_SYSTEM: ContextVar[bool] = ContextVar("app_system", default=False)


def set_actor(user_id: str | None) -> Token[str | None]:
    """Act as ``user_id`` for the rest of this context (a request or a worker task)."""
    return _ACTOR.set(user_id)


def current_actor() -> str | None:
    """The user this context acts as, if any."""
    return _ACTOR.get()


@contextmanager
def acting_as(user_id: str) -> Iterator[None]:
    """Act as ``user_id`` inside the block (a worker working on that user's run or file)."""
    token = _ACTOR.set(user_id)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def set_system() -> Token[bool]:
    """Mark the rest of this context as a system job (see the module docstring)."""
    return _SYSTEM.set(True)


def set_user_scope() -> Token[bool]:
    """Leave the system scope for the rest of this context (a task acting as one user)."""
    return _SYSTEM.set(False)


@contextmanager
def system_scope() -> Iterator[None]:
    """A system job inside the block."""
    token = _SYSTEM.set(True)
    try:
        yield
    finally:
        _SYSTEM.reset(token)


@asynccontextmanager
async def tenant_txn(
    tenant_id: UUID,
    *,
    snapshot: bool = False,
    user: str | None = None,
    system: bool | None = None,
) -> AsyncIterator[AsyncSession]:
    """One transaction == one tenant context, with the acting user (``user``, else the context
    variable) and the scope (``system``, else the context variable).

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
        actor = user if user is not None else _ACTOR.get()
        scope = "system" if (_SYSTEM.get() if system is None else system) else "user"
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :tid, true),"
                " set_config('app.user_id', :uid, true), set_config('app.scope', :scope, true)"
            ),
            {"tid": str(tenant_id), "uid": actor or "", "scope": scope},
        )
        yield session


@asynccontextmanager
async def untenanted_session() -> AsyncIterator[AsyncSession]:
    """A transaction with NO tenant context, for the definer-rights identity lookups only
    (``idp_connection_tenant``, ``login_state_tenant``, ``session_tenant``). RLS shows this
    session zero tenant rows; the lookups return identifiers only."""
    async with _sessions()() as session, session.begin():
        yield session
