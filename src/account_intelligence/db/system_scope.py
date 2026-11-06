"""The two ways out of the tenant and user scope (ADR-0123).

* ``untenanted_session()``: a transaction with NO tenant context, for the definer-rights
  lookups only (``idp_connection_tenant``, ``login_state_tenant``, ``session_tenant``, the
  sweeper's discovery). RLS shows it zero tenant rows; the lookups return identifiers only.
* ``set_system()`` / ``system_scope()``: the rest of this context (or the block) is a system
  job: a tenant transaction sees every user's rows of its tenant (``db.session``'s module
  docstring).

Both bypass a layer of the row-level security, so this module is importable only from the
modules allow-listed in the import-linter contract "only the allow-listed modules leave the
tenant scope" (``pyproject.toml``); everything else uses ``db.session.tenant_txn``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import Token

from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.db.session import _SYSTEM, _sessions


def set_system() -> Token[bool]:
    """Mark the rest of this context as a system job (see the module docstring)."""
    return _SYSTEM.set(True)


@contextmanager
def system_scope() -> Iterator[None]:
    """A system job inside the block."""
    token = _SYSTEM.set(True)
    try:
        yield
    finally:
        _SYSTEM.reset(token)


@asynccontextmanager
async def untenanted_session() -> AsyncIterator[AsyncSession]:
    """A transaction with NO tenant context (see the module docstring)."""
    async with _sessions()() as session, session.begin():
        yield session
