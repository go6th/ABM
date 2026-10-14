"""Harness for the connector layer: fake provider server, fake time, runtime builder.

No test sleeps for real and no test reaches a provider: every provider is an
``httpx.MockTransport`` driven by a script of responses, the clock is a
``FakeClock`` that only moves when a test or the fake ``sleep`` moves it, DNS is a
dictionary, and randomness is seeded.
"""

from __future__ import annotations

import gzip
import json
import random
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from account_intelligence.connectors.base import CallContext, ProviderCode
from account_intelligence.connectors.factory import build_runtime
from account_intelligence.connectors.payloads import InMemoryPayloadStore
from account_intelligence.connectors.security import UrlPolicy
from account_intelligence.connectors.service import ConnectorRuntime
from account_intelligence.connectors.settings import load_providers_config

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "providers" / "contract"
CASSETTES = CONTRACT / "cassettes"
GOLDEN = CONTRACT / "golden"
T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self._now = start
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


class FakeSleep:
    """Records requested sleeps and advances the fake clock instead of waiting."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


class _Truncated(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"data": [{"id": "1"'
        raise httpx.RemoteProtocolError("peer closed connection without sending complete body")


@dataclass
class Reply:
    """One scripted provider response."""

    status: int = 200
    body: Any = None  # dict/list -> JSON; bytes -> raw
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json"
    raise_exc: type[Exception] | None = None
    truncated: bool = False
    gzip: bool = False
    advance_s: float = 0.0  # slow response: moves the fake clock

    @classmethod
    def cassette(cls, code: str, name: str, **kw: Any) -> Reply:
        data = json.loads((CASSETTES / code / f"{name}.json").read_text())
        return cls(status=data["response"]["status"], body=data["response"]["body"], **kw)


class FakeProvider:
    """A scripted provider. ``script`` queues replies; ``fallback`` answers when empty."""

    def __init__(
        self, clock: FakeClock, fallback: Reply | Callable[[httpx.Request], Reply] | None = None
    ):
        self.clock = clock
        self.queue: deque[Reply] = deque()
        self.fallback = fallback
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def script(self, *replies: Reply) -> FakeProvider:
        self.queue.extend(replies)
        return self

    @property
    def calls(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[Any]:
        return [json.loads(r.content) if r.content else None for r in self.requests]

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self.queue:
            reply = self.queue.popleft()
        elif callable(self.fallback):
            reply = self.fallback(request)
        elif self.fallback is not None:
            reply = self.fallback
        else:
            raise AssertionError(f"unscripted request {request.method} {request.url.path}")
        if reply.advance_s:
            self.clock.advance(reply.advance_s)
        if reply.raise_exc is not None:
            raise reply.raise_exc("scripted failure", request=request)  # type: ignore[call-arg]
        headers = {"content-type": reply.content_type, **reply.headers}
        if reply.truncated:
            return httpx.Response(reply.status, headers=headers, stream=_Truncated())
        body = reply.body
        raw = (
            body
            if isinstance(body, bytes)
            else json.dumps(body).encode()
            if body is not None
            else b""
        )
        if reply.gzip:
            raw = gzip.compress(raw)
            headers["content-encoding"] = "gzip"
        return httpx.Response(reply.status, headers=headers, content=raw)


PUBLIC_IP = "93.184.215.14"


def fake_resolver(table: dict[str, list[str]] | None = None) -> Callable[[str], Any]:
    table = {} if table is None else table

    async def resolve(host: str) -> list[str]:
        if host in table:
            return table[host]
        return [PUBLIC_IP]

    return resolve


TEST_ENV = {
    "P_SEARCH_API_KEY": "test-key-search-000",
    "P_SCRAPE_API_KEY": "test-key-scrape-000",
    "P_SIGNALS_API_KEY": "test-key-signals-000",
    "P_SIGNALS_API_TOKEN": "test-token-signals-000",
    "P_PEOPLE_API_KEY": "test-key-people-000",
    "P_ORG_API_KEY": "test-key-org-000",
}


@dataclass
class Harness:
    clock: FakeClock
    sleep: FakeSleep
    providers: dict[ProviderCode, FakeProvider]
    payloads: InMemoryPayloadStore
    resolver_table: dict[str, list[str]]

    def runtime(
        self, txn: Any = None, *, worker_id: str = "worker-a", **overrides: Any
    ) -> ConnectorRuntime:
        if txn is None:
            from account_intelligence.db.session import tenant_txn

            txn = tenant_txn
        rt = build_runtime(
            load_providers_config(),
            txn=txn,
            payloads=self.payloads,
            transports={code: p.transport for code, p in self.providers.items()},
            env=TEST_ENV,
            clock=self.clock,
            sleep=self.sleep,
            rng=random.Random(7),
            url_policy=UrlPolicy(resolver=fake_resolver(self.resolver_table)),
            worker_id=worker_id,
        )
        return replace(rt, **overrides) if overrides else rt


@pytest.fixture
def harness() -> Harness:
    clock = FakeClock()
    return Harness(
        clock=clock,
        sleep=FakeSleep(clock),
        providers={code: FakeProvider(clock) for code in ProviderCode},
        payloads=InMemoryPayloadStore(),
        resolver_table={},
    )


# ------------------------------------------------------------------ database fixtures


def admin_dsn() -> str:
    from tests.conftest import ADMIN_DSN

    return ADMIN_DSN


def make_run(
    tenant: UUID,
    domain: str | None = None,
    *,
    budget_usd: float | None = 5.0,
    call_cap: int | None = None,
) -> tuple[UUID, UUID, str]:
    """Tenant-owned account and account_runs row. Returns (account_id, run_id, domain)."""
    domain = domain or f"acme-{uuid4().hex[:8]}.example.com"
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        account = c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
            " VALUES (%s, 'test-owner', %s) RETURNING id",
            (tenant, domain),
        ).fetchone()[0]
        run = c.execute(
            # cap_required false: these tests exercise the ledger and the budget reserve under
            # the legacy rule; the credit cap gate has its own tests (tests/spend).
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd, tool_call_cap, cap_required)"
            " VALUES (%s, %s, 'test', '{}', 1, %s, %s, %s, false) RETURNING id",
            (tenant, account, "k-" + uuid4().hex, budget_usd, call_cap),
        ).fetchone()[0]
    return account, run, domain


def context(tenant: UUID, account: UUID, run: UUID, domain: str, **kw: Any) -> CallContext:
    return CallContext(
        tenant_id=tenant, run_id=run, account_id=account, account_domain=domain, **kw
    )


def run_row(run: UUID) -> dict[str, Any]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        row = c.execute(
            "SELECT spent_usd, tool_calls_used FROM account_runs WHERE id = %s", (run,)
        ).fetchone()
    return {"spent_usd": row[0], "tool_calls_used": row[1]}


def ledger_rows(run: UUID) -> list[dict[str, Any]]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        cur = c.execute("SELECT * FROM tool_calls WHERE run_id = %s ORDER BY created_at", (run,))
        cols = [d.name for d in cur.description or []]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def event_rows(run: UUID) -> list[dict[str, Any]]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        cur = c.execute("SELECT * FROM tool_call_events WHERE run_id = %s ORDER BY id", (run,))
        cols = [d.name for d in cur.description or []]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def all_texts(values: Iterable[Any]) -> str:
    return "\n".join(json.dumps(v, default=str) for v in values)
