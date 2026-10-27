"""What keeps the Account workspace and every authenticated read fast: a delivered workbook is
opened for its row count once per process (``file_tabs.main_rows``), the read audit skips the
transaction for an event already recorded in its window (``api.guard``), and a pooled connection
is checked only after it sat idle (``db.session.lazy_pre_ping``). SYNTHETIC data only."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from uuid import uuid4

import pytest

from account_intelligence.api import guard
from account_intelligence.replica import file_tabs as FT
from tests.replica.test_file_tabs import CFG, _books


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.gets = 0

    async def get(self, key: str) -> bytes:
        self.gets += 1
        await asyncio.sleep(0)
        return self.objects[key]


@pytest.fixture
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FT, "_CACHE", OrderedDict())
    monkeypatch.setattr(FT, "_COUNTS", OrderedDict())


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh")
async def test_a_delivered_file_is_opened_for_its_count_once() -> None:
    data, model = _books()["people"]
    book = FT.read(data, CFG.rules.workbooks.people.sheet)
    main = book.tab(None)
    assert main is not None and main.table is not None
    tenant = uuid4()
    digest = hashlib.sha256(data).hexdigest()
    key = f"{tenant}/{uuid4()}/{uuid4()}/deliverables/{digest}.xlsx"
    store = _Store()
    store.objects[key] = data
    found = FT.Delivered("d1", "people.xlsx", digest, key, len(main.table.rows), len(data))
    assert await FT.main_rows(store, tenant, found, "people") == len(main.table.rows)
    assert store.gets == 1
    # the book itself may be evicted; the count outlives it and nothing is fetched again
    FT._CACHE.clear()
    assert await FT.main_rows(store, tenant, found, "people") == len(main.table.rows)
    assert store.gets == 1
    # a count the manifest does not state is never taken from the file
    unstated = FT.Delivered("d1", "people.xlsx", digest, key, None, len(data))
    assert await FT.main_rows(store, tenant, unstated, "people") is None
    assert store.gets == 1
    # a manifest that disagrees with the file's table: not this builder's file
    wrong = FT.Delivered("d1", "people.xlsx", digest, key, len(main.table.rows) + 1, len(data))
    assert await FT.main_rows(store, tenant, wrong, "people") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh")
async def test_an_unreadable_file_is_asked_again_not_remembered() -> None:
    tenant = uuid4()
    store = _Store()
    found = FT.Delivered("d1", "people.xlsx", "0" * 64, f"{tenant}/a/b/deliverables/x.xlsx", 3)
    assert await FT.main_rows(store, tenant, found, "people") is None  # the object is missing
    assert FT._COUNTS == {}


def test_a_read_event_recorded_in_its_window_is_not_written_again() -> None:
    guard._READ_SEEN.clear()
    assert guard._read_seen("t1", "k") is False
    guard._READ_SEEN[("t1", "k")] = __import__("time").monotonic() + 60
    assert guard._read_seen("t1", "k") is True
    # the same key under another tenant is another event
    assert guard._read_seen("t2", "k") is False
    # an ended window records again
    guard._READ_SEEN[("t1", "k")] = __import__("time").monotonic() - 1
    assert guard._read_seen("t1", "k") is False
    guard._READ_SEEN.clear()


def test_the_read_event_memory_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    guard._READ_SEEN.clear()
    monkeypatch.setattr(guard, "_READ_SEEN_CAP", 4)
    now = time.monotonic()
    for i in range(4):
        guard._READ_SEEN[("t", str(i))] = now + 60
    assert guard._read_seen("t", "new") is False
    assert len(guard._READ_SEEN) <= 4
    guard._READ_SEEN.clear()


@pytest.mark.asyncio
async def test_a_pooled_connection_is_checked_only_after_it_sat_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from account_intelligence.config import get_settings
    from account_intelligence.db import session as S

    engine = create_async_engine(get_settings().runtime_database_url, pool_size=1, max_overflow=0)
    pings = 0
    real = engine.sync_engine.dialect.do_ping

    def counted(dbapi_connection: object) -> bool:
        nonlocal pings
        pings += 1
        return bool(real(dbapi_connection))

    monkeypatch.setattr(engine.sync_engine.dialect, "do_ping", counted)
    S.lazy_pre_ping(engine, idle_s=0.3)
    try:
        for _ in range(3):
            async with engine.connect() as c:
                await c.exec_driver_sql("SELECT 1")
        assert pings == 0  # handed out again within moments: no check
        await asyncio.sleep(0.4)
        async with engine.connect() as c:
            await c.exec_driver_sql("SELECT 1")
        assert pings == 1  # it sat idle: checked once
    finally:
        await engine.dispose()


def test_a_connection_from_another_event_loop_is_replaced_not_reused() -> None:
    """The worker runs research on several event loops and the tests use one per test: a pooled
    asyncpg connection belongs to the loop that opened it, so the check still runs for it."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from account_intelligence.config import get_settings
    from account_intelligence.db import session as S

    engine = create_async_engine(get_settings().runtime_database_url, pool_size=1, max_overflow=0)
    S.lazy_pre_ping(engine, idle_s=3600)

    async def query() -> int:
        async with engine.connect() as c:
            return int((await c.exec_driver_sql("SELECT 41 + 1")).scalar_one())

    assert asyncio.run(query()) == 42
    assert asyncio.run(query()) == 42  # a second loop, the first one's connection in the pool
    asyncio.run(engine.dispose())
