"""Grading loads a run's sources once per batch and a research grades dozens of batches: a payload
that was read and parsed is not read and parsed again (staging speed audit 2026-10-30).

SYNTHETIC data only; the real ledger on the scratch database, as the other adapter tests."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.factory import build_connectors
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.ports import RunInfo
from tests.connectors.conftest import T0, Harness, Reply, context, make_run
from tests.connectors.conftest import harness as harness  # pytest fixture re-export

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _search(*urls: str) -> dict[str, object]:
    return {
        "search_id": "s1",
        "session_id": "x",
        "results": [
            {"url": u, "title": "Acme", "publish_date": None, "excerpts": ["Acme runs a CRM."]}
            for u in urls
        ],
    }


class CountingStore:
    """The payload store, counting reads."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.gets = 0

    async def put(self, key: str, body: bytes) -> str:
        return str(await self.inner.put(key, body))

    async def get(self, key: str) -> bytes:
        self.gets += 1
        return bytes(await self.inner.get(key))


async def test_a_payload_is_read_and_parsed_once_per_run_however_many_batches_load(
    harness: Harness, tenant_a: UUID, tenant_b: UUID
) -> None:
    account, run, domain = make_run(tenant_a, domain="acme.example")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply(body=_search("https://blog.example.net/one")),
        Reply(body=_search("https://blog.example.net/two", "https://blog.example.net/three")),
    )
    rt = harness.runtime()
    await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("acme one",)), ctx, rt)
    store = CountingStore(harness.payloads)
    adapter = LedgerAdapter(
        tenant_txn, store, build_connectors(load_providers_config()), load_grading_config()
    )
    parsed_on: list[str] = []
    original = adapter._parse

    def spy(*args: Any, **kwargs: Any) -> Any:
        parsed_on.append(threading.current_thread().name)
        return original(*args, **kwargs)

    adapter._parse = spy  # type: ignore[method-assign]
    info = RunInfo(tenant_a, account, run, T0 - timedelta(hours=1), domain, "Acme")

    first = await adapter.sources(info)
    assert [s.url for s in first] == ["https://blog.example.net/one"] and store.gets == 1
    # the parse ran off the event loop's thread: a large feed page no longer stops the loop
    assert parsed_on and threading.current_thread().name not in parsed_on

    again = await adapter.sources(info)  # the next grading batch
    assert again == first and store.gets == 1 and len(parsed_on) == 1

    # a call made since: only its payload is read, the rest comes from the memo, in call order
    await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("acme two",)), ctx, rt)
    third = await adapter.sources(info)
    assert store.gets == 2 and len(parsed_on) == 2
    assert sorted(s.url for s in third) == [
        "https://blog.example.net/one",
        "https://blog.example.net/three",
        "https://blog.example.net/two",
    ]
    assert [s.call_ref for s in third] == sorted(s.call_ref for s in third)

    # nothing of the memo is served for another tenant: its own transaction sees no row of this run
    other = RunInfo(tenant_b, account, run, T0 - timedelta(hours=1), domain, "Acme")
    assert list(await adapter.sources(other)) == []
