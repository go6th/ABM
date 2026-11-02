"""Web lookups for Ask (owner decision 2026-11-02): ``web_search`` and ``read_web_page``.

The owner wants Ask to answer like Claude with the workspace attached: from the workspace, from
the model's own knowledge, and from the web when neither holds the answer — a few lookups, not a
research run. The model decides when to look; the turn's settings bound how often
(``ask_web_searches_max``, ``ask_web_reads_max``).

* ``web_search`` — one open web search (the research pipeline's search provider, ``p_search``,
  through the same connector: its request builder and its spec-checked parser), up to
  ``ask_web_results_max`` results, each a numbered source ``W1``, ``W2`` ... the model cites;
* ``read_web_page`` — more text of a page THIS turn's searches returned (by its ``W`` id or its
  address). Any other address is refused: text in a row or a page can never steer the model to
  send workspace data to a page of its choosing.

Every result passes the archive routes' own guard (``replica.archive_tools.Guard``): a page on a
data provider's or an excluded company's address, or naming an excluded person, is not shown,
and refused text is blanked. A call is priced from ``config/providers.yaml`` (no price is written
here) and the turn's cost carries it. The calls go straight to the provider, outside the research
call ledger, which belongs to runs.

GUARANTEE(test_web_lookups_are_bounded_guarded_priced_and_cited)
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from account_intelligence.connectors.base import Operation, ProviderCode, RawResponse
from account_intelligence.connectors.factory import auth_headers, provider_configured
from account_intelligence.connectors.models import ReadPageArgs, WebPageRecord, WebSearchArgs
from account_intelligence.connectors.providers.p_search import SearchConnector
from account_intelligence.connectors.settings import ProviderCfg, load_providers_config
from account_intelligence.replica.answers import Acct, Row
from documents.tables import QueryError
from obs.logging import logger

log = logger("replica.web_tools")

NAMES = ("web_search", "read_web_page")
CODE = ProviderCode.P_SEARCH
EXCERPT_CHARS = 800  # one excerpt as the model reads it
PAGE_CHARS = 12_000  # one page read as the model reads it
ROW_TEXT_CHARS = 2_000  # what a cited web source keeps

WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the live web. Use it only when neither the workspace nor your own knowledge holds"
        " the answer, or to check something that may have changed recently (news, a launch, a"
        " leadership change, a figure after your training). Give 1 to 3 short queries (3 to 6"
        " words each) and one sentence saying what you need. Each result has an id (W1, W2 ...),"
        " a title, an address, a date when the page has one, and excerpts; cite what you use as"
        " [W1]."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 200},
                "minItems": 1,
                "maxItems": 3,
            },
            "objective": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "required": ["queries", "objective"],
    },
}
READ_WEB_PAGE_TOOL: dict[str, Any] = {
    "name": "read_web_page",
    "description": (
        "Read more of one page that web_search returned in this answer, named by its id (W2) or"
        " its address, with one sentence saying what you need from it. Other pages cannot be"
        " read."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "page": {"type": "string", "minLength": 1, "maxLength": 2048},
            "objective": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "required": ["page", "objective"],
    },
}
WEB_TOOLS = (WEB_SEARCH_TOOL, READ_WEB_PAGE_TOOL)


def web_rules(max_searches: int, max_reads: int) -> str:
    """The prompt lines of a turn that has the web tools (route rules, not the cached part)."""
    return (
        "Web lookups: the workspace comes first for facts about the accounts and your own"
        " knowledge for general facts. Use `web_search` only for what neither holds or for what"
        f" may have changed since your training; at most {max_searches} searches and"
        f" {max_reads} page reads in this answer, so stop once you have the answer. Cite a web"
        " fact by its [W#] id and say how recent it is. A web fact is Probable unless it comes"
        " from the account's own site or an official filing. When a web result and a workspace"
        " row disagree, show both; the row wins for the account."
    )


def search_cost(cfg: ProviderCfg, args: WebSearchArgs) -> Decimal:
    """One search's price from the provider's configured cost model (0 when it states none)."""
    op = cfg.operations.get(Operation.WEB_SEARCH)
    if op is None:
        return Decimal(0)
    base = (op.usd_by_mode or {}).get(args.mode, op.usd or 0.0)
    extra = max(0, args.max_results - (op.included_results or args.max_results))
    return Decimal(str(base)) + Decimal(str(op.usd_per_additional_result or 0.0)) * extra


def read_cost(cfg: ProviderCfg) -> Decimal:
    """One page read's price from the provider's configured cost model."""
    op = cfg.operations.get(Operation.READ_PAGE)
    return Decimal(str(op.usd or 0.0)) if op is not None else Decimal(0)


def configured(env: Mapping[str, str] | None = None) -> ProviderCfg | None:
    """The search provider's configuration when its credentials are set, else None (the turn
    then has no web tools, and the prompt never mentions them)."""
    try:
        cfg = load_providers_config().provider(CODE)
    except Exception:  # noqa: BLE001 - no provider configuration: no web tools
        return None
    return cfg if provider_configured(cfg, env if env is not None else os.environ) else None


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class WebTools:
    """The two web tools of one Ask turn (see the module docstring). ``rows`` holds every
    result shown so far, by its ``W`` id: the answer's citations are checked against them."""

    def __init__(
        self,
        cfg: ProviderCfg,
        anchor: Acct,
        guards: Callable[[], Awaitable[Sequence[Any]]],
        *,
        max_searches: int,
        max_reads: int,
        max_results: int,
        env: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        self.anchor = anchor
        self.guards = guards
        self.max_searches = max_searches
        self.max_reads = max_reads
        self.max_results = max_results
        self.env = env if env is not None else os.environ
        self.transport = transport
        self.connector = SearchConnector()
        self.rows: dict[str, Row] = {}
        self._by_url: dict[str, str] = {}
        self.searches = 0
        self.reads = 0
        self.cost_usd = Decimal(0)
        self._guards: Sequence[Any] | None = None

    def rules(self) -> str:
        """This turn's prompt lines for the tools."""
        return web_rules(self.max_searches, self.max_reads)

    async def run(self, name: str, payload: dict[str, Any]) -> Any:
        """One call; a ``QueryError`` carries what the model can fix."""
        if name == "web_search":
            return await self._search(payload)
        if name == "read_web_page":
            return await self._read(payload)
        raise QueryError("unknown_tool", f"no tool named {name!r}")

    async def _search(self, payload: dict[str, Any]) -> Any:
        if self.searches >= self.max_searches:
            raise QueryError(
                "web_budget_used",
                f"{self.max_searches} web searches are the most one answer may run; answer now",
            )
        queries = tuple(str(q)[:200] for q in payload.get("queries") or () if str(q).strip())
        objective = str(payload.get("objective") or "").strip()[:500] or None
        try:
            args = WebSearchArgs(
                queries=queries[:3], objective=objective, max_results=self.max_results
            )
        except Exception as error:  # noqa: BLE001 - a bad argument is the model's to fix
            raise QueryError("bad_arguments", f"web_search arguments: {error}") from None
        self.searches += 1
        records = await self._call(Operation.WEB_SEARCH, args, search_cost(self.cfg, args))
        results = []
        for record in records:
            shown = await self._shown(record)
            if shown is None:
                continue
            rid, text = shown
            results.append(
                {
                    "id": rid,
                    "title": record.title_text,
                    "url": record.url,
                    "date": record.publish_date,
                    "excerpts": [_clip(e, EXCERPT_CHARS) for e in text],
                }
            )
        return {"results": results, "searches_left": self.max_searches - self.searches}

    async def _read(self, payload: dict[str, Any]) -> Any:
        if self.reads >= self.max_reads:
            raise QueryError(
                "web_budget_used",
                f"{self.max_reads} page reads are the most one answer may run; answer now",
            )
        page = str(payload.get("page") or "").strip()
        rid = page.upper() if page.upper() in self.rows else self._by_url.get(page)
        if rid is None:
            raise QueryError(
                "unknown_page",
                "only a page this answer's web_search returned can be read; name it by its W id",
            )
        url = str(self.rows[rid].url)
        objective = str(payload.get("objective") or "").strip()[:500] or None
        try:
            args = ReadPageArgs(url=url, objective=objective, full_content=True)
        except Exception as error:  # noqa: BLE001 - an address the provider cannot read
            raise QueryError("bad_arguments", f"read_web_page: {error}") from None
        self.reads += 1
        records = await self._call(Operation.READ_PAGE, args, read_cost(self.cfg))
        for record in records:
            shown = await self._shown(record, rid=rid)
            if shown is None:
                continue
            _, text = shown
            body = record.full_content_text or "\n".join(text)
            clean, _ = await self._scrub(body)
            return {
                "id": rid,
                "title": record.title_text,
                "url": record.url,
                "text": _clip(str(clean or ""), PAGE_CHARS),
                "reads_left": self.max_reads - self.reads,
            }
        return {"id": rid, "text": "", "note": "the page could not be read"}

    async def _call(
        self, op: Operation, args: WebSearchArgs | ReadPageArgs, price: Decimal
    ) -> list[WebPageRecord]:
        """One provider exchange through the connector's own request and parser."""
        request = self.connector.build_request(op, args, None)
        http = self.cfg.http
        timeout = httpx.Timeout(
            http.total_s, connect=http.connect_s, read=http.read_s, write=http.write_s,
            pool=http.pool_s,
        )  # fmt: skip
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self.cfg.base_url, timeout=timeout, transport=self.transport
            ) as client:
                response = await client.request(
                    request.method,
                    request.path,
                    params=dict(request.params) if request.params else None,
                    json=dict(request.json_body) if request.json_body is not None else None,
                    headers=auth_headers(self.cfg, self.env),
                )
        except httpx.HTTPError as error:
            log.warning(
                "ask.web_failed", extra={"operation": op.value, "error_class": type(error).__name__}
            )
            raise QueryError(
                "web_unavailable", "the web lookup failed; answer without it"
            ) from None
        raw = RawResponse(
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            body=response.content[: http.max_bytes],
            headers=dict(response.headers),
            latency_ms=round((time.monotonic() - started) * 1000),
            request_path=request.path,
        )
        if response.status_code != 200:
            if self.cfg.bill_on_failure:
                self.cost_usd += price
            log.warning(
                "ask.web_failed", extra={"operation": op.value, "http_status": response.status_code}
            )
            raise QueryError("web_unavailable", "the web lookup failed; answer without it")
        self.cost_usd += price
        base = {
            "provider_code": CODE,
            "operation": op,
            "retrieved_at": datetime.now(UTC),
            "raw_ref": f"ask-web:{uuid.uuid4()}",
        }
        try:
            parsed = self.connector.parse(op, args, raw, base, None)
        except Exception as error:  # noqa: BLE001 - an unreadable reply is no result
            log.warning(
                "ask.web_failed", extra={"operation": op.value, "error_class": type(error).__name__}
            )
            raise QueryError("web_unavailable", "the web reply could not be read") from None
        return [r for r in parsed.records if isinstance(r, WebPageRecord)]

    async def _guard_list(self) -> Sequence[Any]:
        if self._guards is None:
            try:
                self._guards = list(await self.guards())
            except Exception:  # noqa: BLE001 - no guard, no web result (fail closed)
                log.warning("ask.web_guard_failed")
                self._guards = [_Closed()]
        return self._guards

    async def _scrub(self, value: Any) -> tuple[Any, bool]:
        refused = False
        for guard in await self._guard_list():
            value, paths = guard.scrub(value)
            refused = refused or bool(paths)
        return value, refused

    async def _shown(
        self, record: WebPageRecord, rid: str | None = None
    ) -> tuple[str, list[str]] | None:
        """The result's id and its excerpts as shown, registering it as a citable row; None when
        the guard hides it."""
        url = record.url
        if urlsplit(url).scheme not in ("http", "https"):
            return None
        texts = [record.title_text, *record.excerpts_text]
        if any(g.hides(*texts, url=url) for g in await self._guard_list()):
            return None
        excerpts, _ = await self._scrub(list(record.excerpts_text))
        excerpts = [str(e) for e in excerpts if e]
        if rid is None:
            rid = self._by_url.get(url) or f"W{len(self.rows) + 1}"
        host = (urlsplit(url).hostname or url).removeprefix("www.")
        self.rows[rid] = Row(
            f"web:{uuid.uuid5(uuid.NAMESPACE_URL, url)}",
            "web",
            self.anchor,
            _clip(" ".join(excerpts), ROW_TEXT_CHARS) or (record.title_text or host),
            _clip(record.title_text or host, 200),
            "Found on the web for this answer",
            _published(record.publish_date),
            url,
            None,
            None,
            {"web": True},
        )
        self._by_url[url] = rid
        return rid, excerpts


class _Closed:
    """The guard of a turn whose guard could not be built: nothing is shown."""

    def hides(self, *texts: str | None, url: str | None = None) -> bool:
        return True

    def scrub(self, value: Any) -> tuple[Any, list[str]]:
        return value, []
