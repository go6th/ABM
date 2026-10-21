"""The hard limits on one run's spending (Phase 4B section 4, ``connectors.spend_guard``): the
platform's dollar cap counts provider and model spend together and is checked before the
provider is called; the same normalised query is paid at most ``repeat_call_limit`` times; every
ledger event moves the run's heartbeat; and neither stop is a connector error, so it ends the run
instead of failing one row."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import ConnectorError
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.spend_guard import (
    DollarCapReached,
    RepeatedCallStopped,
    query_key,
)
from tests.connectors.conftest import Harness, Reply, admin_dsn, context, make_run

pytestmark = [pytest.mark.requires_postgres]


def _sql(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture
def platform() -> Iterator[None]:
    (before,) = _sql("SELECT run_dollar_cap_usd, repeat_call_limit FROM platform_defaults")
    yield
    _sql(
        "UPDATE platform_defaults SET run_dollar_cap_usd = %s, repeat_call_limit = %s",
        before[0],
        before[1],
    )


def test_the_query_key_ignores_case_spacing_and_paging() -> None:
    a = query_key("p_search", "web_search", {"queries": ["Acme  Funding"], "max_results": 10})
    b = query_key("p_search", "web_search", {"queries": ["acme funding"], "max_results": 5})
    c = query_key("p_search", "web_search", {"queries": ["acme layoffs"]})
    assert a == b != c
    assert not issubclass(DollarCapReached, ConnectorError)
    assert not issubclass(RepeatedCallStopped, ConnectorError)


@pytest.mark.asyncio
async def test_a_run_stops_partial_at_the_dollar_cap_counting_model_spend(
    harness: Harness, tenant_a: UUID, platform: None
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    _sql("UPDATE platform_defaults SET run_dollar_cap_usd = 0.10")
    # the run's model replies already cost most of the cap
    _sql(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, cost_usd, cost_known) VALUES (%s, %s, 'k', 'extract', 'extractor',"
        " 'fake-model', 'model', 0.098, true)",
        tenant_a,
        run,
    )
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "web_search"))
    with pytest.raises(DollarCapReached) as stopped:
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding round",)), ctx,
            harness.runtime(),
        )  # fmt: skip
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0  # refused before any call
    assert str(stopped.value.cap) == "0.10"
    assert _sql("SELECT count(*) FROM tool_call_events WHERE run_id = %s", run) == [(0,)]


@pytest.mark.asyncio
async def test_with_the_caps_off_neither_the_dollar_cap_nor_the_repeat_limit_stops_a_run(
    harness: Harness, tenant_a: UUID, platform: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_CAPS", "off")
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    _sql("UPDATE platform_defaults SET run_dollar_cap_usd = 0.10, repeat_call_limit = 1")
    _sql(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, cost_usd, cost_known) VALUES (%s, %s, 'k', 'extract', 'extractor',"
        " 'fake-model', 'model', 0.50, true)",
        tenant_a,
        run,
    )
    search = harness.providers[ProviderCode.P_SEARCH]
    for _ in range(3):
        search.script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime()
    for text in ("acme funding round", "Acme funding round", "ACME funding  round"):
        await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=(text,)), ctx, rt)
    assert search.calls == 3  # past the dollar cap and paid three times for one query
    (moved,) = _sql("SELECT progress_at IS NOT NULL FROM account_runs WHERE id = %s", run)
    assert moved == (True,)  # the heartbeat still moves


@pytest.mark.asyncio
async def test_the_same_query_paid_past_the_limit_stops_the_run(
    harness: Harness, tenant_a: UUID, platform: None
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    _sql("UPDATE platform_defaults SET repeat_call_limit = 3")
    search = harness.providers[ProviderCode.P_SEARCH]
    for _ in range(4):
        search.script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime()
    # four spellings of one query: different requests, the same question
    spellings = (
        "acme funding round",
        "Acme funding round",
        "ACME funding  round",
        "acme Funding round",
    )
    for text in spellings[:3]:
        await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=(text,)), ctx, rt)
    with pytest.raises(RepeatedCallStopped) as stopped:
        await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=(spellings[3],)), ctx, rt)
    assert search.calls == 3 and stopped.value.times == 4
    # an identical call is a replay, never a new payment, so it never counts
    replay = await call_operation(
        Operation.WEB_SEARCH, WebSearchArgs(queries=(spellings[0],)), ctx, rt
    )
    assert replay.replayed and search.calls == 3
    (moved,) = _sql("SELECT progress_at IS NOT NULL FROM account_runs WHERE id = %s", run)
    assert moved == (True,)  # the heartbeat moved with every ledger event


@pytest.mark.asyncio
async def test_the_dollar_cap_ends_the_run_partial_and_keeps_what_it_holds(
    harness: Harness, tenant_a: UUID, platform: None
) -> None:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.platform.hermetic import MemoryPayloadStore, ScriptedLlm
    from account_intelligence.workflow.deps import RunDeps
    from account_intelligence.workflow.runner import _stop_unexpected
    from account_intelligence.workflow.spec import load_spec
    from tests.connectors.conftest import FakeClock

    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    _sql("UPDATE platform_defaults SET run_dollar_cap_usd = 0.10")
    _sql(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, cost_usd, cost_known) VALUES (%s, %s, 'k', 'extract', 'extractor',"
        " 'fake-model', 'model', 0.093, true)",
        tenant_a,
        run,
    )
    search = harness.providers[ProviderCode.P_SEARCH]
    search.script(Reply.cassette("p_search", "web_search"))
    rt = harness.runtime()
    await call_operation(
        Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding round",)), ctx, rt
    )
    with pytest.raises(DollarCapReached) as stopped:
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=(f"acme ceo {uuid4().hex[:8]}",)), ctx, rt
        )
    deps = RunDeps(
        tenant_a, run, account, domain, tenant_txn, ScriptedLlm(lambda *a: "{}"),
        MemoryPayloadStore(), FakeClock(), load_spec(),
    )  # fmt: skip
    assert (await deps.model("abm_pass2_extract", "sys", "under the cap")).text == "{}"
    _sql(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, cost_usd, cost_known) VALUES (%s, %s, 'k2', 'extract', 'extractor',"
        " 'fake-model', 'model', 0.002, true)",
        tenant_a,
        run,
    )
    with pytest.raises(DollarCapReached):  # at the cap, a model call is refused too
        await deps.model("abm_pass2_extract", "sys", "at the cap")
    await _stop_unexpected(deps, stopped.value)
    assert _sql("SELECT status, finished_reason FROM account_runs WHERE id = %s", run) == [
        ("PARTIAL", "DollarCapReached")
    ]
    assert _sql("SELECT count(*) FROM tool_calls WHERE run_id = %s", run) == [(1,)]  # kept
