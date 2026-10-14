"""Hermetic data providers (``CONNECTORS=hermetic``, development and tests only): synthetic replies
for any account, so the whole research path runs offline on the local stack with the hermetic
model (``LLM_FAKE=hermetic``).

Each provider gets an ``httpx.MockTransport`` that answers from the contract cassettes in
``config/hermetic/providers`` with the account's own domain put in: one news story, one careers
page and job posting, one technology detection; the organisation lookup finds no match (so no
people call is made); the people provider is unsupported as in production. Host names resolve
to a fixed public address without any DNS query. Nothing leaves the process: no request reaches
a provider and no key is used (the placeholder keys below exist only so the runtime's
configured-provider rule holds). ``app.container`` refuses this mode outside ``dev`` and
``test``, like the hermetic model.

GUARANTEE(test_hermetic_providers_answer_offline_for_any_account)
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import unquote

import httpx

from account_intelligence.config import CONFIG_DIR
from account_intelligence.connectors.base import ProviderCode

CASSETTES = CONFIG_DIR / "hermetic" / "providers"
PUBLIC_ADDRESS = "93.184.215.14"
_DOMAIN = re.compile(r"\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b")
PLACEHOLDER_KEYS = {
    "P_SEARCH_API_KEY": "hermetic-search",
    "P_SCRAPE_API_KEY": "hermetic-scrape",
    "P_SIGNALS_API_KEY": "hermetic-signals",
    "P_SIGNALS_API_TOKEN": "hermetic-signals-token",  # nosec B105 -- a placeholder no provider sees.
    "P_PEOPLE_API_KEY": "hermetic-people",
    "P_ORG_API_KEY": "hermetic-org",
}
PAD = (
    " The team works with the business on delivery, testing and support across the platform,"
    " and the role reports to the head of engineering."
)


@lru_cache(maxsize=16)
def _cassette_text(name: str) -> str:
    return (CASSETTES / f"{name}.json").read_text()


def cassette(name: str) -> dict[str, Any]:
    """A fresh copy of one cassette body (``p_search__web_search`` and so on)."""
    body: dict[str, Any] = json.loads(_cassette_text(name))
    return copy.deepcopy(body)


def _domain_of(*texts: str) -> str:
    for text in texts:
        match = _DOMAIN.search(text.lower())
        if match:
            return match.group(1)
    return "account.example"


def _name_of(domain: str) -> str:
    return domain.split(".")[0].replace("-", " ").title()


def _json(request: httpx.Request) -> dict[str, Any]:
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _reply(body: Any) -> httpx.Response:
    return httpx.Response(200, json=body)


def search(request: httpx.Request) -> httpx.Response:
    """Web search and page reads: a careers page, a posting, or a news story on the account."""
    body = _json(request)
    if request.url.path.endswith("/extract"):
        url = (body.get("urls") or ["https://account.example/"])[0]
        domain = _domain_of(url)
        out = cassette("p_search__read_page")
        out["errors"] = []
        out["results"] = [
            {
                "url": url,
                "title": f"{_name_of(domain)} careers",
                "excerpts": [f"{_name_of(domain)} careers page."],
                "full_content": f"{_name_of(domain)} careers page. Open roles are listed here.",
            }
        ]
        return _reply(out)
    query = " ".join(body.get("search_queries") or [str(body.get("objective") or "")])
    domains = (body.get("advanced_settings") or {}).get("source_policy", {}).get(
        "include_domains"
    ) or []
    domain = _domain_of(*domains, query)
    name = _name_of(domain)
    out = cassette("p_search__web_search")
    if "careers" in query:
        hit = {
            "url": f"https://{domain}/careers",
            "title": f"Careers at {name}",
            "excerpts": [f"Join {name}."],
            "publish_date": None,
        }
    elif query.endswith(" jobs"):
        hit = {
            "url": f"https://{domain}/careers/jobs/s1",
            "title": "Salesforce Developer",
            "excerpts": ["Build Salesforce triggers."],
            "publish_date": None,
        }
    else:
        hit = {
            "url": f"https://news.{domain}/story-1",
            "title": f"{name} expands",
            "excerpts": [f"{name} opened a new engineering office in Pune in 2026."],
            "publish_date": "2026-09-01",
        }
    out["results"] = [hit]
    return _reply(out)


def signals(request: httpx.Request) -> httpx.Response:
    """Job openings, news events and technology detections for ``/companies/<domain>/...``."""
    path = unquote(request.url.path)
    domain = _domain_of(path)
    name = _name_of(domain)
    if path.endswith("job_openings"):
        out = cassette("p_signals__job_openings")
        item = out["data"][0]
        item["attributes"].update(
            title="Salesforce Developer",
            normalized_title="Salesforce Developer",
            description="You will build Salesforce Apex triggers as a developer." + PAD,
            url=f"https://{domain}/careers/jobs/s1",
            status="open",
            posted_at="2026-08-01",
            first_seen_at="2026-08-01T00:00:00Z",
            location="Pune, India",
        )
        item["id"] = "s1"
        out["data"] = [item]
    elif path.endswith("news_events"):
        out = cassette("p_signals__news_events")
        out["data"][0]["attributes"].update(
            summary=f"{name} opens an engineering office in Pune.",
            article_sentence=f"{name} opened a new engineering office in Pune in 2026.",
            found_at="2026-09-01T00:00:00Z",
        )
        out["data"] = out["data"][:1]
    else:
        out = cassette("p_signals__tech_detections")
        out["data"] = out["data"][:1]
    out.setdefault("meta", {})["count"] = len(out["data"])
    return _reply(out)


def scrape(request: httpx.Request) -> httpx.Response:
    """A job page read: the posting's text."""
    body = _json(request)
    url = str(body.get("url") or "https://account.example/careers/jobs/s1")
    out = cassette("p_scrape__scrape_job_page")
    out["data"]["markdown"] = "You will build Salesforce Apex triggers as a developer." + PAD
    out["data"]["html"] = None
    out["data"].setdefault("metadata", {}).update(
        sourceURL=url, url=url, statusCode=200, title="Salesforce Developer"
    )
    return _reply(out)


def org(request: httpx.Request) -> httpx.Response:
    """The organisation lookup finds no match, so people search ends without a people call."""
    asked = _json(request).get("organizations") or []
    out = cassette("p_org__org_resolve")
    out["matched_count"] = 0
    out["organizations"] = [{"input": dict(entry), "attributes": {}} for entry in asked]
    return _reply(out)


def unsupported(request: httpx.Request) -> httpx.Response:
    """The people provider: no call is expected (its operations are unsupported)."""
    return httpx.Response(501, json={"error": "not supported in the hermetic stack"})


HANDLERS: Mapping[ProviderCode, Callable[[httpx.Request], httpx.Response]] = {
    ProviderCode.P_SEARCH: search,
    ProviderCode.P_SIGNALS: signals,
    ProviderCode.P_SCRAPE: scrape,
    ProviderCode.P_ORG: org,
    ProviderCode.P_PEOPLE: unsupported,
}


def transports() -> dict[ProviderCode, httpx.AsyncBaseTransport]:
    """One mock transport per provider (no request leaves the process)."""
    return {code: httpx.MockTransport(handler) for code, handler in HANDLERS.items()}


async def resolve(host: str) -> list[str]:
    """Every host is the same public address; no DNS query is made."""
    return [PUBLIC_ADDRESS]
