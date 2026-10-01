"""Shared fixtures: DB availability detection, tenant fixtures, role-safety checks."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

DB_URL = os.environ.get("DATABASE_URL", "postgresql://app_rw:app_rw_dev@localhost:5432/ai")
MIGRATOR_URL = os.environ.get("TEST_MIGRATOR_DSN", "postgresql://postgres:dev@localhost:5432/ai")


def _db_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(MIGRATOR_URL, connect_timeout=3):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="live Postgres not available")


@pytest.fixture(scope="session")
def event_loop() -> AsyncIterator[asyncio.AbstractEventLoop]:
    """Session-scoped event loop for engine reuse."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def tenant_a_id() -> UUID:
    """Fresh tenant id for tenant A per test."""
    return uuid4()


@pytest.fixture()
def tenant_b_id() -> UUID:
    """Fresh tenant id for tenant B per test."""
    return uuid4()
