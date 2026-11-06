"""A scheduled tick that cannot reach the database answers 503 with one WARNING line, never a
traceback at ERROR (staging 2026-11-04: a stopped Cloud SQL instance made every tick a traceback,
several a minute, for as long as it stayed stopped)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from account_intelligence.api.routes.tasks import _tenant_ids

pytestmark = pytest.mark.asyncio


class _Row:
    tenant_id = "00000000-0000-0000-0000-000000000001"


async def test_the_tenant_list_is_read_when_the_database_answers() -> None:
    class Session:
        async def execute(self, *_: object) -> list[_Row]:
            return [_Row()]

    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        yield Session()

    assert [str(t) for t in await _tenant_ids(factory)] == [_Row.tenant_id]


async def test_an_unreachable_database_is_a_503_and_one_warning(caplog) -> None:  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        raise ConnectionRefusedError("instance is stopped")
        yield  # pragma: no cover

    caplog.set_level(logging.WARNING)
    with pytest.raises(HTTPException) as got:
        await _tenant_ids(factory)
    assert got.value.status_code == 503
    records = [r for r in caplog.records if r.getMessage() == "tasks.db_unavailable"]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].error_class == "ConnectionRefusedError"  # type: ignore[attr-defined]
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
