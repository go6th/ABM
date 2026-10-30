"""The content reader loads its stored objects side by side (staging speed audit 2026-10-30): a
research's hundred search payloads were a hundred object-store round trips one after another."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from datamodel.run_ledger import LedgerCall
from exports.readers.pg import PgContentReader


class SlowStore:
    def __init__(self) -> None:
        self.gets = 0

    async def get(self, key: str) -> bytes:
        self.gets += 1
        await asyncio.sleep(0.02)
        if key == "missing":
            raise LookupError(key)
        body = {"results": [{"url": f"https://{key}.example.com/p", "title": "T", "excerpts": []}]}
        return json.dumps(body).encode()


def call(ref: str | None, operation: str = "web_search") -> LedgerCall:
    return LedgerCall(
        at=datetime(2026, 10, 30, tzinfo=UTC),
        operation=operation,
        step=None,
        queries=(),
        terms=(),
        domain=None,
        since=None,
        result_count=1,
        cost_usd=0.0,
        response_ref=ref,
    )


@pytest.mark.asyncio
async def test_the_search_payloads_of_a_build_are_read_together_and_keep_their_order() -> None:
    store = SlowStore()
    reader: Any = PgContentReader.__new__(PgContentReader)
    reader.payloads = store
    ledger = [call(f"k{i}") for i in range(20)]
    ledger.insert(3, call("missing"))  # a payload that cannot be read leaves its call without hosts
    ledger.insert(5, call("x", operation="job_openings"))  # not a search: never read
    started = time.monotonic()
    out = await reader._pull_calls(ledger, None)
    assert time.monotonic() - started < 0.15  # 20 reads of 0.02 s one at a time: 0.4 s
    assert store.gets == 21
    assert [c.operation for c in out][5] == "job_openings" and out[5].hits == ()
    assert out[3].hits == () and out[0].hits[0].host == "k0.example.com"
    assert [c.hits[0].host for c in out if c.hits] == [f"k{i}.example.com" for i in range(20)]
