"""LangGraph Postgres checkpointer on a dedicated, tenant-bound connection.

The checkpoint tables are library-owned; migration 0002 puts a tenant-prefix RLS policy on them
(``thread_id = '<tenant>:<run>'``). The connection is private to one slice: the tenant setting is
session-scoped (autocommit has no long transaction) and the connection is closed when the slice
ends, so it never returns to a pool carrying a tenant. ``setup()`` runs once in the migrator,
never here.

Where it connects (``checkpoint_target``, ADR-0125):

* staging and production: Cloud SQL IAM database authentication, like the main engine. The
  Cloud SQL Python connector the main engine uses serves only asyncpg and pg8000, and LangGraph's
  saver needs a psycopg ``AsyncConnection``, so the connector cannot be used here. Instead the
  saver connects to the instance's private IP (``CLOUD_SQL_PRIVATE_IP``) as the service's IAM
  database user (``DB_IAM_USER``) with a short-lived OAuth access token (scope
  ``sqlservice.login``, about an hour) as the password: no long-lived password exists. TLS is
  ``verify-ca`` against the server CA when ``CLOUD_SQL_CA_FILE`` names one, else ``require``
  (encrypted; the instance refuses anything else). VERIFY(V-SQL-2): manual IAM login over the
  private IP needs the real instance.
* the local profiles, and a cloud release that still carries one: ``CHECKPOINT_DATABASE_URL``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

SQL_LOGIN_SCOPE = "https://www.googleapis.com/auth/sqlservice.login"
_REFRESH_BEFORE = timedelta(minutes=5)
CLOUD_PROFILES = frozenset({"staging", "production"})


@dataclass(frozen=True)
class CheckpointIam:
    """Where the cloud checkpointer signs in with IAM (see the module docstring)."""

    host: str
    dbname: str
    user: str
    ca_file: str | None


def checkpoint_target(env: Mapping[str, str]) -> str | CheckpointIam:
    """IAM on the private IP in the cloud when the instance address is configured, else the
    ``CHECKPOINT_DATABASE_URL`` (``""`` when there is neither)."""
    ip, user, db = env.get("CLOUD_SQL_PRIVATE_IP"), env.get("DB_IAM_USER"), env.get("DB_NAME")
    if env.get("APP_ENV", "dev") in CLOUD_PROFILES and ip and user and db:
        return CheckpointIam(ip, db, user, env.get("CLOUD_SQL_CA_FILE") or None)
    return env.get("CHECKPOINT_DATABASE_URL", "")


def _default_credentials() -> Any:
    import google.auth

    credentials, _ = google.auth.default(scopes=[SQL_LOGIN_SCOPE])
    return credentials


class IamTokens:
    """The service account's access token, refreshed five minutes before it expires."""

    def __init__(self, credentials: Callable[[], Any] = _default_credentials) -> None:
        self._make = credentials
        self._credentials: Any = None
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        c = self._credentials
        if c is None or not c.token or c.expiry is None:
            return False
        expiry = c.expiry if c.expiry.tzinfo else c.expiry.replace(tzinfo=UTC)
        return bool(expiry - datetime.now(UTC) > _REFRESH_BEFORE)

    async def token(self) -> str:
        """A token valid for at least five more minutes (refreshed off the event loop)."""
        async with self._lock:
            if not self._fresh():
                from google.auth.transport.requests import Request

                if self._credentials is None:
                    self._credentials = self._make()
                await asyncio.to_thread(self._credentials.refresh, Request())
            return str(self._credentials.token)


_TOKENS = IamTokens()


async def _connect(target: str | CheckpointIam) -> psycopg.AsyncConnection[Any]:
    options: dict[str, Any] = {"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0}
    if isinstance(target, str):
        return await psycopg.AsyncConnection.connect(target, **options)
    tls: dict[str, Any] = (
        {"sslmode": "verify-ca", "sslrootcert": target.ca_file}
        if target.ca_file
        else {"sslmode": "require"}
    )
    return await psycopg.AsyncConnection.connect(
        host=target.host,
        port=5432,
        dbname=target.dbname,
        user=target.user,
        password=await _TOKENS.token(),
        **tls,
        **options,
    )


def checkpointer_factory(target: str | CheckpointIam):  # type: ignore[no-untyped-def]
    """``(tenant_id) -> async context manager yielding an AsyncPostgresSaver``; ``target`` is
    a psycopg URL or a ``CheckpointIam`` (see ``checkpoint_target``)."""

    @asynccontextmanager
    async def open_saver(tenant_id: UUID) -> AsyncIterator[AsyncPostgresSaver]:
        """A saver on a fresh connection bound to the tenant, closed when the slice ends."""
        conn = await _connect(target)
        try:
            await conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
            yield AsyncPostgresSaver(conn)
        finally:
            await conn.close()

    return open_saver
