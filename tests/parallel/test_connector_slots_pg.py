"""The governor at the connector (D-43, P4, P8) and the Postgres unit event store (P9, P13), on
real Postgres with the scripted synthetic providers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import RateLimited
from account_intelligence.connectors.governance import UNIT_PRIORITY, SlotUnavailable
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from parallel.config import load_parallel_config
from parallel.models import EventKind, UnitEvent, WorkUnit
from parallel.singleflight import InProcessSingleFlight
from parallel.store import PgEventStore, measured_durations
from tests.connectors.conftest import Harness, Reply, context, make_run
from tests.connectors.conftest import harness as harness_fixture

harness = harness_fixture  # the connector suite's fixture, shared here

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingGovernor:
    """Counts slots and their concurrency; can refuse."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.active = 0
        self.peak = 0
        self.slots: list[tuple[str, int]] = []
        self.signals: list[tuple[str, str]] = []

    @asynccontextmanager
    async def _slot(self, provider: str, priority: int) -> Any:
        if self.refuse:
            raise SlotUnavailable(provider, 30.0)
        self.slots.append((provider, priority))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield 0.0
        finally:
            self.active -= 1

    def slot(self, ctx: Any, provider_code: str, priority: int) -> Any:
        return self._slot(provider_code, priority)

    async def signal(self, ctx: Any, provider_code: str, kind: str) -> None:
        self.signals.append((provider_code, kind))


SEARCH = WebSearchArgs(queries=("synthetic slot",))


async def test_call_operation_holds_a_slot_only_around_live_calls(
    harness: Harness, tenant_a: UUID
) -> None:
    gov = RecordingGovernor()
    account, run, domain = make_run(tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime(governor=gov)
    ctx = context(tenant_a, account, run, domain)
    UNIT_PRIORITY.set(1)
    first = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert first.status == "done" and gov.slots == [("p_search", 1)]
    assert gov.signals == [("p_search", "clean")]
    replay = await call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt)
    assert replay.replayed and gov.slots == [("p_search", 1)]  # a replay never takes a slot
    assert gov.active == 0


async def test_no_slot_raises_before_the_ledger_claim(harness: Harness, tenant_a: UUID) -> None:
    from tests.spend.conftest import admin

    account, run, domain = make_run(tenant_a)
    rt = harness.runtime(governor=RecordingGovernor(refuse=True))
    with pytest.raises(SlotUnavailable):
        await call_operation(
            Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run, domain), rt
        )
    # queueing never consumes a ledger attempt or a reservation
    assert admin("SELECT count(*) FROM tool_call_events WHERE run_id = %s", run) == [(0,)]
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0


async def test_rate_limit_signal_reaches_the_governor(harness: Harness, tenant_a: UUID) -> None:
    gov = RecordingGovernor()
    account, run, domain = make_run(tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(Reply(status=429, body={}))
    rt = harness.runtime(governor=gov)
    with pytest.raises(RateLimited):
        await call_operation(
            Operation.WEB_SEARCH, SEARCH, context(tenant_a, account, run, domain), rt
        )
    assert gov.signals == [("p_search", "rate_limited")] and gov.active == 0


async def test_concurrent_identical_operations_share_one_pull(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    sf = InProcessSingleFlight()
    rt = harness.runtime(single_flight=sf)
    ctx = context(tenant_a, account, run, domain)
    results = await asyncio.gather(
        *(call_operation(Operation.WEB_SEARCH, SEARCH, ctx, rt) for _ in range(6))
    )
    assert harness.providers[ProviderCode.P_SEARCH].calls == 1  # pull once (P8)
    assert len({r.call_refs for r in results}) == 1


async def test_pg_event_store_round_trip_and_single_terminal(tenant_a: UUID) -> None:
    from tests.spend.conftest import admin

    _, run, _ = make_run(tenant_a)
    store = PgEventStore(tenant_txn, tenant_a, run, "worker-a")
    units = [
        WorkUnit("body|a", "a", "a", routes=("web_search",), priority=1),
        WorkUnit("body|b", "b", "b", depends_on=("body|a",)),
    ]
    await store.define(units)
    await store.define(units)  # idempotent
    assert admin("SELECT count(*) FROM work_units WHERE run_id = %s", run) == [(2,)]
    await store.append(UnitEvent("body|a", EventKind.QUEUED, T0))
    await store.append(
        UnitEvent(
            "body|a",
            EventKind.STARTED,
            T0 + timedelta(seconds=1),
            lease_wait_s=0.5,
            provider_code="p_search",
        )
    )
    await store.append(
        UnitEvent("body|a", EventKind.FINISHED, T0 + timedelta(seconds=9)),
        {"completed_stages": ["a"]},
    )
    await asyncio.gather(
        *(store.append(UnitEvent("body|a", EventKind.BLOCKED, T0, "late")) for _ in range(5))
    )  # duplicates and races insert nothing
    loaded = await store.load()
    assert [e.event.event for e in loaded] == [
        EventKind.QUEUED,
        EventKind.STARTED,
        EventKind.FINISHED,
    ]
    assert loaded[2].result == {"completed_stages": ["a"]} and loaded[1].event.lease_wait_s == 0.5
    from tests.conftest import _make_tenant

    intruder = PgEventStore(tenant_txn, _make_tenant("intruder"), run, "x")
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):  # another tenant cannot attach events to this unit
        await intruder.append(UnitEvent("body|a", EventKind.QUEUED, T0))
    assert await intruder.load() == []  # and sees none of them
    cfg = replace(load_parallel_config(), min_samples_for_p95=1)
    async with tenant_txn(tenant_a) as s:
        durations = await measured_durations(s, cfg)
    assert durations.p95("a") == (8.0, True)
    assert durations.p95("never_seen") == (cfg.unmeasured_p95_s, False)
