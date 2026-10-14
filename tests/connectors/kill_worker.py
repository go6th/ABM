"""Subprocess worker for kill tests: runs one call and dies at a chosen point.

Usage: python -m tests.connectors.kill_worker <mode> <tenant> <account> <run> <domain> <calls_file>
       <payload_dir>
Modes: ``after_response`` dies after the first provider response, before the ledger is
finalised; ``mid_pagination`` dies after the second page's response. Every provider
request is appended to ``calls_file`` so the parent can count calls across processes.
The process exits with ``os._exit(137)`` (no cleanup, like a SIGKILL).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx

from account_intelligence.connectors.base import CallContext, Operation, ProviderCode, SystemClock
from account_intelligence.connectors.factory import build_runtime
from account_intelligence.connectors.models import FeedArgs, WebSearchArgs
from account_intelligence.connectors.payloads import LocalPayloadStore
from account_intelligence.connectors.providers.p_signals import SignalsConnector
from account_intelligence.connectors.security import UrlPolicy
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "providers" / "contract" / "cassettes"


def news_page(page: int, total_pages: int) -> dict:  # type: ignore[type-arg]
    body = json.loads((CONTRACT / "p_signals" / "news_events.json").read_text())["response"]["body"]
    body = copy.deepcopy(body)
    body["data"][0]["id"] = f"event-{page}"
    body["meta"]["count"] = total_pages
    return body


def search_body() -> dict:  # type: ignore[type-arg]
    return json.loads((CONTRACT / "p_search" / "web_search.json").read_text())["response"]["body"]


def transport(calls_file: Path, total_pages: int) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        with calls_file.open("a") as f:
            f.write(f"{request.url.path}?{request.url.query.decode()}\n")
        if request.url.path.endswith("/news_events"):
            page = int(request.url.params["page"])
            return httpx.Response(200, json=news_page(page, total_pages))
        return httpx.Response(200, json=search_body())

    return httpx.MockTransport(handle)


async def main(
    mode: str, tenant: str, account: str, run: str, domain: str, calls: str, store: str
) -> None:
    seen = {"responses": 0}
    die_after = 1 if mode == "after_response" else 2

    async def hook() -> None:
        seen["responses"] += 1
        if seen["responses"] == die_after:
            os._exit(137)

    tr = transport(Path(calls), total_pages=3)
    rt = build_runtime(
        load_providers_config(),
        txn=tenant_txn,
        payloads=LocalPayloadStore(store),
        transports={code: tr for code in ProviderCode},
        env={"P_SEARCH_API_KEY": "k", "P_SIGNALS_API_KEY": "k"},
        clock=SystemClock(),
        url_policy=UrlPolicy(resolver=_public),
        worker_id="worker-killed",
    )
    rt = replace(
        rt,
        after_response_hook=hook,
        connectors={**rt.connectors, ProviderCode.P_SIGNALS: SignalsConnector(page_limit=1)},
    )
    ctx = CallContext(UUID(tenant), UUID(run), UUID(account), domain)
    if mode == "after_response":
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding",)), ctx, rt
        )
    else:
        await call_operation(Operation.NEWS_EVENTS, FeedArgs(domain=domain), ctx, rt)
    sys.exit(0)  # not reached when the hook kills the process


async def _public(host: str) -> list[str]:
    return ["93.184.215.14"]


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    asyncio.run(main(*sys.argv[1:8]))
