"""A source link as the client sees it: "6th / LinkedIn", opening the page the research read.

Whatever connector brought a row in, the client sees who found it (6th) and the website it came
from, never the connector (owner request 2026-10-21). The link opens the original page directly.
A link on a data provider's own host (``documents.gate.provider_hosts``) is never shown: it would
name the provider, and it is not the original website. Such a row reads "6th agents" without a
link. Site names are worked out in code from the host (well-known sites by name, every other site
by its registrable domain); a name that would still carry a data tool name falls back to
"6th agents" too.

GUARANTEE(test_a_source_link_names_6th_and_the_site_never_the_connector)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

BRAND = "6th"
NO_SITE = "6th agents"

# well-known sites by registrable domain (or by the full host where one domain has several)
SITES: dict[str, str] = {
    "linkedin.com": "LinkedIn",
    "lever.co": "Lever",
    "greenhouse.io": "Greenhouse",
    "myworkdayjobs.com": "Workday",
    "myworkdaysite.com": "Workday",
    "workday.com": "Workday",
    "smartrecruiters.com": "SmartRecruiters",
    "icims.com": "iCIMS",
    "ashbyhq.com": "Ashby",
    "jobvite.com": "Jobvite",
    "taleo.net": "Taleo",
    "successfactors.com": "SuccessFactors",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "monster.com": "Monster",
    "dice.com": "Dice",
    "builtin.com": "Built In",
    "wellfound.com": "Wellfound",
    "smartsearchonline.com": "SmartSearch",
    "prnewswire.com": "PR Newswire",
    "businesswire.com": "Business Wire",
    "globenewswire.com": "GlobeNewswire",
    "finance.yahoo.com": "Yahoo Finance",
    "yahoo.com": "Yahoo",
    "investing.com": "Investing.com",
    "simplywall.st": "Simply Wall St",
    "sec.gov": "SEC",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "wsj.com": "The Wall Street Journal",
    "forbes.com": "Forbes",
    "cnbc.com": "CNBC",
    "fiercehealthcare.com": "Fierce Healthcare",
    "fiercebiotech.com": "Fierce Biotech",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "x.com": "X",
    "twitter.com": "X",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "medium.com": "Medium",
    "wikipedia.org": "Wikipedia",
    "crunchbase.com": "Crunchbase",
    "g2.com": "G2",
    "salesforce.com": "Salesforce",
    "servicenow.com": "ServiceNow",
    "sap.com": "SAP",
}
# second-level suffixes under which the registrable domain is three labels (example.co.uk)
_TWO_PART = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.in",
        "co.jp", "co.kr", "co.za", "com.br", "com.mx", "com.sg", "com.hk", "com.cn", "com.tr",
    }
)  # fmt: skip


@dataclass(frozen=True)
class SourceLink:
    """What a source cell shows (``label``) and where it opens (``href``, None: no link)."""

    label: str
    href: str | None


def registrable(host: str) -> str:
    """``careers.example.com`` -> ``example.com``; ``news.example.co.uk`` -> ``example.co.uk``."""
    labels = host.lower().rstrip(".").split(".")
    take = 3 if ".".join(labels[-2:]) in _TWO_PART and len(labels) >= 3 else 2
    return ".".join(labels[-take:])


def _provider_domains() -> tuple[str, ...]:
    from documents.gate import provider_hosts

    try:
        return provider_hosts()
    except Exception:  # noqa: BLE001 - no provider config: the tool-name check still applies
        return ()


def site_name(host: str) -> str:
    """The website a host belongs to, as a reader would name it."""
    host = host.lower().rstrip(".")
    bare = host.removeprefix("www.").removeprefix("m.")
    domain = registrable(bare)
    return SITES.get(bare) or SITES.get(domain) or domain


def source_link(url: object) -> SourceLink | None:
    """The client's view of one source URL; None when there is no web address at all."""
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or "." not in host:
        return None
    domain = registrable(host)
    if any(domain == p or domain.endswith("." + p) for p in _provider_domains()):
        return SourceLink(NO_SITE, None)
    name = site_name(host)
    from agents.naming import client_hits, tool_name_hits

    if tool_name_hits(name) or client_hits(name):
        return SourceLink(NO_SITE, None)
    return SourceLink(f"{BRAND} / {name}", url.strip())


def relabel_answer(answer: Any) -> Any:
    """A stored Ask answer with every cited source's link worked out again: answers saved before
    the label existed (or naming a provider's page) read "6th / <site>" when they are reopened."""
    if not isinstance(answer, dict) or not isinstance(answer.get("sources"), list):
        return answer
    sources = []
    for source in answer["sources"]:
        if isinstance(source, dict):
            link = source_link(source.get("url"))
            source = {
                **source,
                "url": link.href if link else None,
                "label": link.label if link else None,
            }
        sources.append(source)
    return {**answer, "sources": sources}
