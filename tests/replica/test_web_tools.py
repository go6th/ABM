"""Ask's web lookups: bounded per answer, guarded like the archive, priced from the provider
configuration and cited as [W#] sources (a fake provider, no network).

GUARANTEE(test_web_lookups_are_bounded_guarded_priced_and_cited)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest

from account_intelligence.connectors.base import ProviderCode
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.replica import answers as A
from account_intelligence.replica import web_tools as WEB
from account_intelligence.replica.model_answer import stream_with_model
from account_intelligence.replica.tools import AccessTools
from documents.tables import QueryError

CFG = load_providers_config().provider(ProviderCode.P_SEARCH)
ENV = {"P_SEARCH_API_KEY": "test-search-key-123"}


class Guard:
    """Hides any page that names "Secret Person"; scrubs nothing."""

    def hides(self, *texts: str | None, url: str | None = None) -> bool:
        return any("Secret Person" in (t or "") for t in texts)

    def scrub(self, value: Any) -> tuple[Any, list[str]]:
        return value, []


def _acct() -> A.Acct:
    return A.Acct(
        id=uuid4(), domain="acme.example", name="Acme", lanes=[], cadence=None,
        last_refresh=None, refreshes=1,
    )  # fmt: skip


def _transport(seen: list[dict[str, Any]]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(
            {"path": request.url.path, "body": body, "key": request.headers.get("x-api-key")}
        )
        if request.url.path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "search_id": "s1",
                    "results": [
                        {
                            "url": "https://news.example/acme-buys-beta",
                            "title": "Acme buys Beta",
                            "publish_date": "2026-10-30",
                            "excerpts": ["Acme agreed to buy Beta for $2B."],
                        },
                        {
                            "url": "https://gossip.example/x",
                            "title": "About Secret Person",
                            "publish_date": None,
                            "excerpts": ["Secret Person moved."],
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "extract_id": "e1",
                "results": [
                    {
                        "url": body["urls"][0],
                        "title": "Acme buys Beta",
                        "publish_date": "2026-10-30",
                        "excerpts": ["Acme agreed to buy Beta."],
                        "full_content": "Acme agreed to buy Beta for $2B in cash.",
                    }
                ],
                "errors": [],
            },
        )

    return httpx.MockTransport(handle)


def _web(seen: list[dict[str, Any]], **kw: Any) -> WEB.WebTools:
    async def guards() -> list[Any]:
        return [Guard()]

    return WEB.WebTools(
        CFG,
        _acct(),
        guards,
        max_searches=kw.get("max_searches", 2),
        max_reads=kw.get("max_reads", 1),
        max_results=5,
        env=ENV,
        transport=_transport(seen),
    )


@pytest.mark.asyncio
async def test_web_lookups_are_bounded_guarded_priced_and_cited() -> None:
    seen: list[dict[str, Any]] = []
    web = _web(seen, max_searches=1, max_reads=1)
    out = await web.run("web_search", {"queries": ["Acme acquisition"], "objective": "deals"})
    # the page naming an excluded person is never shown, and is no citable source
    assert [r["id"] for r in out["results"]] == ["W1"]
    assert set(web.rows) == {"W1"} and web.rows["W1"].kind == "web"
    assert web.rows["W1"].url == "https://news.example/acme-buys-beta"
    assert seen[0]["key"] == ENV["P_SEARCH_API_KEY"] and seen[0]["path"] == "/v1/search"
    with pytest.raises(QueryError) as budget:
        await web.run("web_search", {"queries": ["more"], "objective": "more"})
    assert budget.value.code == "web_budget_used"
    # only a page this answer's searches returned can be read
    with pytest.raises(QueryError) as other:
        await web.run("read_web_page", {"page": "https://evil.example/?d=1", "objective": "x"})
    assert other.value.code == "unknown_page"
    page = await web.run("read_web_page", {"page": "w1", "objective": "the price"})
    assert "$2B in cash" in page["text"]
    # priced from the provider configuration, never a number written in the code
    args = WebSearchArgs(queries=("Acme acquisition",), objective="deals", max_results=5)
    assert web.cost_usd == WEB.search_cost(CFG, args) + WEB.read_cost(CFG)
    assert web.cost_usd > Decimal(0)


@pytest.mark.asyncio
async def test_a_failed_guard_shows_no_web_result() -> None:
    async def broken() -> list[Any]:
        raise RuntimeError("no policy")

    web = WEB.WebTools(
        CFG, _acct(), broken, max_searches=1, max_reads=1, max_results=5, env=ENV,
        transport=_transport([]),
    )  # fmt: skip
    out = await web.run("web_search", {"queries": ["Acme"], "objective": "news"})
    assert out["results"] == [] and web.rows == {}


def test_web_tools_join_every_route_only_when_present() -> None:
    web = _web([])
    with_web = AccessTools(lambda: None, [], [], web=web)
    without = AccessTools(lambda: None, [], [])
    for route in ("prose", "mixed", "tabular"):
        names = {t["name"] for t in with_web.offered(route)}
        assert set(WEB.NAMES) <= names
        assert not set(WEB.NAMES) & {t["name"] for t in without.offered(route)}
    assert WEB.configured({}) is None  # no key: no web tools


@pytest.mark.asyncio
async def test_a_web_result_is_cited_as_a_web_source_and_marks_the_web_used() -> None:
    seen: list[dict[str, Any]] = []
    web = _web(seen)
    acct = web.anchor
    now = datetime.now(UTC)
    fact = A.Row(
        f"fact:{uuid4()}", "fact", acct, "Acme runs Salesforce", "Acme", now, None, "confirmed"
    )
    ws = A.Workspace([acct], [fact], [], [], [], [], {})
    q, local = A.draft_local("What did Acme buy recently?", ws, now)

    class Provider:
        async def stream(self, *_a: Any, **k: Any):  # type: ignore[no-untyped-def]
            assert "Web lookups:" in str(k["system"])  # the web rules ride only with the tools
            result = await web.run("web_search", {"queries": ["Acme buys"], "objective": "deals"})
            yield _Event("tool", tool={"name": "web_search", "input": {}, "result": result})
            yield _Event("text", "Acme agreed to buy Beta [W1]. Acme runs Salesforce [R1].")
            yield _Event("usage", usage=None)

    events = [
        e
        async for e in stream_with_model(
            Provider(),
            object(),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=AccessTools(lambda: None, [], [], web=web).offered("prose"),
            web=web,
        )  # fmt: skip
    ]
    kind, reply = events[-1]
    assert kind == "done"
    final = A.finish(reply.draft, q, ws, now, None)
    kinds = {s["kind"]: s for s in final["sources"]}
    assert kinds["web"]["url"] == "https://news.example/acme-buys-beta"
    assert final["used"]["web"] == "used" and final["used"]["workspace"] == "used"


class _Event:
    def __init__(self, kind: str, text: str = "", usage: Any = None, tool: Any = None) -> None:
        self.kind, self.text, self.usage, self.tool = kind, text, usage, tool
