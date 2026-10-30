"""The Sources table, and the generic knowledge of who publishes what that News shares.

``PUBLISHERS`` is a data table of well-known domains (regulators, newswires, trade press,
aggregators, job boards, detection sites): publisher name, source type and tier. Nothing in it
names an account. The account's own sites come from the ``WorkspaceProfile`` (``domains``,
``careers_hosts``, ``publishers``); a host in no table gets a readable name derived from its
registrable domain."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.dates import date_from_text, date_from_url, is_collection_date
from exports.repair.keys import canonical_url, host_of, registrable
from exports.repair.records import ArticleRecord, SourceRecord

# ---- source types ---------------------------------------------------------------------------
SEC_FILING = "SEC filing"
REGULATOR = "Regulator filing"
PRESS_RELEASE = "Company press release"
COMPANY_SITE = "Company site"
CAREERS_SITE = "Careers site"
NEWSWIRE = "Newswire"
TRADE_PRESS = "Trade press"
AGGREGATOR = "Aggregator"
PROFILE = "Profile"
TECH_DETECTION = "Tech detection"
OTHER = "Other"
SOURCE_TYPES = (
    SEC_FILING, REGULATOR, PRESS_RELEASE, COMPANY_SITE, CAREERS_SITE, NEWSWIRE, TRADE_PRESS,
    AGGREGATOR, PROFILE, TECH_DETECTION, OTHER,
)  # fmt: skip

# ---- tiers: how far a source can carry a fact (primary: Confirmed; trade: Probable) -----------
PRIMARY = "primary"
TRADE = "trade"
AGG = "aggregator"
OTHER_TIER = "other"
TIERS = (PRIMARY, TRADE, AGG, OTHER_TIER)


@dataclass(frozen=True)
class SiteInfo:
    """What a well-known host publishes."""

    publisher: str
    source_type: str
    tier: str


def _site(publisher: str, source_type: str, tier: str) -> SiteInfo:
    return SiteInfo(publisher, source_type, tier)


# host suffix -> info; the longest matching suffix wins
PUBLISHERS: dict[str, SiteInfo] = {
    # regulators and filings
    "sec.gov": _site("U.S. Securities and Exchange Commission", SEC_FILING, PRIMARY),
    "fda.gov": _site("U.S. Food and Drug Administration", REGULATOR, PRIMARY),
    "ftc.gov": _site("U.S. Federal Trade Commission", REGULATOR, PRIMARY),
    "justice.gov": _site("U.S. Department of Justice", REGULATOR, PRIMARY),
    "sedarplus.ca": _site("SEDAR+", REGULATOR, PRIMARY),
    "companieshouse.gov.uk": _site("Companies House", REGULATOR, PRIMARY),
    "esma.europa.eu": _site("European Securities and Markets Authority", REGULATOR, PRIMARY),
    # newswires: they carry releases from many companies, so a record must say it is primary
    "prnewswire.com": _site("PR Newswire", NEWSWIRE, TRADE),
    "businesswire.com": _site("Business Wire", NEWSWIRE, TRADE),
    "globenewswire.com": _site("GlobeNewswire", NEWSWIRE, TRADE),
    "accesswire.com": _site("ACCESSWIRE", NEWSWIRE, TRADE),
    "prweb.com": _site("PRWeb", NEWSWIRE, TRADE),
    "newswire.ca": _site("Canada Newswire", NEWSWIRE, TRADE),
    # general and trade press
    "reuters.com": _site("Reuters", TRADE_PRESS, TRADE),
    "apnews.com": _site("Associated Press", TRADE_PRESS, TRADE),
    "bloomberg.com": _site("Bloomberg", TRADE_PRESS, TRADE),
    "wsj.com": _site("The Wall Street Journal", TRADE_PRESS, TRADE),
    "ft.com": _site("Financial Times", TRADE_PRESS, TRADE),
    "nytimes.com": _site("The New York Times", TRADE_PRESS, TRADE),
    "cnbc.com": _site("CNBC", TRADE_PRESS, TRADE),
    "forbes.com": _site("Forbes", TRADE_PRESS, TRADE),
    "fortune.com": _site("Fortune", TRADE_PRESS, TRADE),
    "businessinsider.com": _site("Business Insider", TRADE_PRESS, TRADE),
    "bizjournals.com": _site("American City Business Journals", TRADE_PRESS, TRADE),
    "fiercehealthcare.com": _site("Fierce Healthcare", TRADE_PRESS, TRADE),
    "fiercebiotech.com": _site("Fierce Biotech", TRADE_PRESS, TRADE),
    "fiercepharma.com": _site("Fierce Pharma", TRADE_PRESS, TRADE),
    "medtechdive.com": _site("MedTech Dive", TRADE_PRESS, TRADE),
    "biopharmadive.com": _site("BioPharma Dive", TRADE_PRESS, TRADE),
    "healthcaredive.com": _site("Healthcare Dive", TRADE_PRESS, TRADE),
    "modernhealthcare.com": _site("Modern Healthcare", TRADE_PRESS, TRADE),
    "techcrunch.com": _site("TechCrunch", TRADE_PRESS, TRADE),
    "computerworld.com": _site("Computerworld", TRADE_PRESS, TRADE),
    "cio.com": _site("CIO", TRADE_PRESS, TRADE),
    "informationweek.com": _site("InformationWeek", TRADE_PRESS, TRADE),
    "theregister.com": _site("The Register", TRADE_PRESS, TRADE),
    "siliconangle.com": _site("SiliconANGLE", TRADE_PRESS, TRADE),
    "venturebeat.com": _site("VentureBeat", TRADE_PRESS, TRADE),
    "supplychaindive.com": _site("Supply Chain Dive", TRADE_PRESS, TRADE),
    # aggregators, transcript and quote sites, company databases
    "finance.yahoo.com": _site("Yahoo Finance", AGGREGATOR, AGG),
    "yahoo.com": _site("Yahoo", AGGREGATOR, AGG),
    "seekingalpha.com": _site("Seeking Alpha", AGGREGATOR, AGG),
    "fool.com": _site("The Motley Fool", AGGREGATOR, AGG),
    "investing.com": _site("Investing.com", AGGREGATOR, AGG),
    "marketscreener.com": _site("MarketScreener", AGGREGATOR, AGG),
    "marketbeat.com": _site("MarketBeat", AGGREGATOR, AGG),
    "stocktitan.net": _site("Stock Titan", AGGREGATOR, AGG),
    "simplywall.st": _site("Simply Wall St", AGGREGATOR, AGG),
    "zacks.com": _site("Zacks", AGGREGATOR, AGG),
    "benzinga.com": _site("Benzinga", AGGREGATOR, AGG),
    "tipranks.com": _site("TipRanks", AGGREGATOR, AGG),
    "gurufocus.com": _site("GuruFocus", AGGREGATOR, AGG),
    "tradingview.com": _site("TradingView", AGGREGATOR, AGG),
    "msn.com": _site("MSN", AGGREGATOR, AGG),
    "news.google.com": _site("Google News", AGGREGATOR, AGG),
    "crunchbase.com": _site("Crunchbase", AGGREGATOR, AGG),
    "zoominfo.com": _site("ZoomInfo", AGGREGATOR, AGG),
    "dnb.com": _site("Dun & Bradstreet", AGGREGATOR, AGG),
    "owler.com": _site("Owler", AGGREGATOR, AGG),
    "g2.com": _site("G2", AGGREGATOR, AGG),
    "wikipedia.org": _site("Wikipedia", AGGREGATOR, AGG),
    # job boards (the account's own board is recognised by ``careers_hosts``)
    "indeed.com": _site("Indeed", AGGREGATOR, AGG),
    "glassdoor.com": _site("Glassdoor", AGGREGATOR, AGG),
    "ziprecruiter.com": _site("ZipRecruiter", AGGREGATOR, AGG),
    "myworkdayjobs.com": _site("Workday job board", CAREERS_SITE, OTHER_TIER),
    "myworkdaysite.com": _site("Workday job board", CAREERS_SITE, OTHER_TIER),
    "greenhouse.io": _site("Greenhouse job board", CAREERS_SITE, OTHER_TIER),
    "lever.co": _site("Lever job board", CAREERS_SITE, OTHER_TIER),
    "icims.com": _site("iCIMS job board", CAREERS_SITE, OTHER_TIER),
    "smartrecruiters.com": _site("SmartRecruiters job board", CAREERS_SITE, OTHER_TIER),
    "taleo.net": _site("Taleo job board", CAREERS_SITE, OTHER_TIER),
    "ashbyhq.com": _site("Ashby job board", CAREERS_SITE, OTHER_TIER),
    "jobvite.com": _site("Jobvite job board", CAREERS_SITE, OTHER_TIER),
    "successfactors.com": _site("SuccessFactors job board", CAREERS_SITE, OTHER_TIER),
    "successfactors.eu": _site("SuccessFactors job board", CAREERS_SITE, OTHER_TIER),
    "workable.com": _site("Workable job board", CAREERS_SITE, OTHER_TIER),
    "recruitee.com": _site("Recruitee job board", CAREERS_SITE, OTHER_TIER),
    # profiles and social
    "linkedin.com": _site("LinkedIn", PROFILE, OTHER_TIER),
    "github.com": _site("GitHub", OTHER, OTHER_TIER),
    "youtube.com": _site("YouTube", OTHER, OTHER_TIER),
    "x.com": _site("X", OTHER, OTHER_TIER),
    "twitter.com": _site("X", OTHER, OTHER_TIER),
    "facebook.com": _site("Facebook", OTHER, OTHER_TIER),
    "medium.com": _site("Medium", OTHER, OTHER_TIER),
    "reddit.com": _site("Reddit", OTHER, OTHER_TIER),
    # technology detection
    "builtwith.com": _site("BuiltWith", TECH_DETECTION, AGG),
    "wappalyzer.com": _site("Wappalyzer", TECH_DETECTION, AGG),
    "similartech.com": _site("SimilarTech", TECH_DETECTION, AGG),
    "stackshare.io": _site("StackShare", TECH_DETECTION, AGG),
    "datanyze.com": _site("Datanyze", TECH_DETECTION, AGG),
}

_NEWSROOM_LABELS = frozenset({"news", "investors", "ir", "press", "media", "newsroom"})
_CAREERS_LABELS = frozenset({"jobs", "careers", "career"})
_JOB_PATH = re.compile(
    r"/(?:jobs?|careers?|openings?|vacanc(?:y|ies)|viewjob|job-search)(?:/|$)|/requisition",
    re.I,
)
_NEWSROOM_PATH = re.compile(
    r"/(?:newsroom|press[-_]?releases?|news[-_]?releases?|press)(?:/|$)", re.I
)
_CODE = re.compile(r"^\s*S[-\s]?0*(\d+)\s*$", re.I)
READ = "Read"
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)")


# ---- host knowledge -------------------------------------------------------------------------
def _bare(host: str) -> str:
    host = host.lower().strip().strip(".")
    return host[4:] if host.startswith("www.") else host


def _suffix_match(host: str, suffix: str) -> bool:
    suffix = _bare(suffix)
    return bool(suffix) and (host == suffix or host.endswith("." + suffix))


def _lookup(host: str) -> SiteInfo | None:
    hits = [s for s in PUBLISHERS if _suffix_match(host, s)]
    return PUBLISHERS[max(hits, key=len)] if hits else None


def _own_domains(profile: WorkspaceProfile) -> set[str]:
    return {_bare(d) for d in (*profile.domains, profile.domain) if d and d.strip()}


def is_own_host(host: str, profile: WorkspaceProfile) -> bool:
    """Whether ``host`` belongs to the account (its domains or its careers board)."""
    bare = _bare(host)
    if not bare:
        return False
    domains = _own_domains(profile) | {_bare(h) for h in profile.careers_hosts}
    return any(_suffix_match(bare, d) for d in domains)


def _path_of(url: str) -> str:
    try:
        return urlsplit(url if "://" in url else "https://" + url).path
    except ValueError:
        return ""


def _is_newsroom(host: str, url: str) -> bool:
    return _bare(host).split(".")[0] in _NEWSROOM_LABELS or bool(
        _NEWSROOM_PATH.search(_path_of(url))
    )


def _is_careers_host(host: str, profile: WorkspaceProfile) -> bool:
    bare = _bare(host)
    return any(_suffix_match(bare, h) for h in profile.careers_hosts)


def is_job_page(url: str, profile: WorkspaceProfile) -> bool:
    """A page that is a job posting or a job board page: a posting-looking path, a known applicant
    system host, a ``jobs.``/``careers.`` host, or a host of the account's careers board."""
    host = host_of(url)
    if _is_careers_host(host, profile):
        return True
    site = _lookup(host)
    if site and site.source_type == CAREERS_SITE:
        return True
    if host.split(".")[0] in _CAREERS_LABELS:
        return True
    return bool(_JOB_PATH.search(_path_of(url)))


def readable_name(host: str) -> str:
    """A readable publisher from the registrable domain (``trade-weekly.co.uk`` -> Trade Weekly)."""
    reg = registrable(_bare(host))
    label = reg.split(".")[0] if reg else ""
    words = [w for w in re.split(r"[-_]+", label) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or reg or core.NOT_FOUND


def publisher_for(url: str, profile: WorkspaceProfile, given: str = "") -> str:
    """Who published ``url``: a profile override, the account itself (for its own domains), the
    record's own publisher, the generic table, else a name derived from the domain."""
    host = host_of(url)
    overrides = [(s, p) for s, p in profile.publishers if _suffix_match(host, s) and p.strip()]
    if overrides:
        return max(overrides, key=lambda pair: len(pair[0]))[1].strip()
    if _is_careers_host(host, profile):
        return f"{profile.account_name} (careers site)"
    if is_own_host(host, profile):
        if _is_newsroom(host, url):
            return f"{profile.account_name} Newsroom"
        return f"{profile.account_name} (company site)"
    if given.strip():
        return given.strip()
    site = _lookup(host)
    return site.publisher if site else readable_name(host)


_KIND_TYPES = {
    "sec filing": SEC_FILING, "sec_filing": SEC_FILING, "filing": SEC_FILING,
    "regulator filing": REGULATOR, "company press release": PRESS_RELEASE,
    "press release": PRESS_RELEASE, "company site": COMPANY_SITE, "careers site": CAREERS_SITE,
    "newswire": NEWSWIRE, "trade press": TRADE_PRESS, "trade": TRADE_PRESS,
    "aggregator": AGGREGATOR, "aggregator (labelled)": AGGREGATOR, "profile": PROFILE,
    "tech detection": TECH_DETECTION, "website detection": TECH_DETECTION, "other": OTHER,
}  # fmt: skip
_KIND_FALLBACK = {"primary": COMPANY_SITE, "secondary": OTHER}


def source_type_for(url: str, profile: WorkspaceProfile, given: str = "", kind: str = "") -> str:
    """The Source type: the given word (when it is in the vocabulary), else inferred from the
    host and path, else from the stored kind (primary, secondary, aggregator), else Other."""
    for word in (given, kind):
        mapped = _KIND_TYPES.get(word.strip().lower())
        if mapped:
            return mapped
    host = host_of(url)
    site = _lookup(host)
    if site and site.source_type in (SEC_FILING, REGULATOR):
        return site.source_type
    if is_own_host(host, profile):
        if _is_careers_host(host, profile) or is_job_page(url, profile):
            return CAREERS_SITE
        return PRESS_RELEASE if _is_newsroom(host, url) else COMPANY_SITE
    if site:
        return site.source_type
    if is_job_page(url, profile):
        return CAREERS_SITE
    return _KIND_FALLBACK.get(kind.strip().lower(), OTHER)


def tier_for(url: str, profile: WorkspaceProfile, record_tier: str = "") -> str:
    """How far this source can carry a fact. The account's own sites and regulators are always
    primary; a known aggregator or transcript site is never better than aggregator; otherwise the
    record's own tier counts; otherwise the generic table; otherwise ``other``."""
    host = host_of(url)
    site = _lookup(host)
    if is_own_host(host, profile) or (site and site.tier == PRIMARY):
        return PRIMARY
    if site and site.tier == AGG:
        return AGG
    given = record_tier.strip().lower()
    if given in TIERS:
        return given
    return site.tier if site else OTHER_TIER


# ---- the table ------------------------------------------------------------------------------
def norm_code(code: str) -> str:
    """``S-0001``, ``s1`` and ``S1`` all become ``S1``; ``Read`` stays ``Read``; anything else is
    returned stripped."""
    m = _CODE.match(code or "")
    if m:
        return f"S{int(m.group(1))}"
    return (code or "").strip()


def _is_real_code(code: str) -> bool:
    return bool(_CODE.match(code or ""))


def _valid_url(raw: str) -> str | None:
    """The canonical URL when ``raw`` is a web address, else None."""
    if not raw or not raw.strip():
        return None
    text = raw.strip().lower()
    if _SCHEME.match(text) and not text.startswith(("http://", "https://")):
        return None
    canon = canonical_url(raw)
    host = host_of(canon)
    return canon if "." in host and " " not in host else None


def _published_cell(rec: SourceRecord) -> str:
    """Published ISO, or Unknown. The collection day is accepted only when the URL or the title
    proves it; a missing date is read from the URL when it carries one."""
    proof = {date_from_url(rec.url), date_from_text(rec.title)}
    if rec.published and not (
        is_collection_date(rec.published, rec.collected) and rec.published not in proof
    ):
        return core.iso(rec.published)
    return core.iso(date_from_url(rec.url))


def _clean_title(rec: SourceRecord, canon: str) -> str:
    title = " ".join((rec.title or "").split())
    if not title or title == rec.url.strip() or canonical_url(title) == canon:
        return ""
    return title


def _merge(group: list[SourceRecord]) -> SourceRecord:
    """One record from the rows that share a URL: the coded one leads, the others fill gaps."""
    ordered = sorted(group, key=lambda r: _code_key(r.code if _is_real_code(r.code) else ""))
    lead = ordered[0]
    used: list[str] = []
    for r in ordered:
        used.extend(u for u in r.used_in if u and u not in used)
    collected = [r.collected for r in ordered if r.collected]
    return SourceRecord(
        code=lead.code,
        url=lead.url,
        title=next((r.title for r in ordered if r.title.strip()), ""),
        publisher=next((r.publisher for r in ordered if r.publisher.strip()), ""),
        published=next((r.published for r in ordered if r.published), None),
        kind=next((r.kind for r in ordered if r.kind.strip()), ""),
        collected=min(collected) if collected else None,
        used_in=tuple(used),
        read_only=all(r.read_only for r in ordered),
    )


def _code_key(code: str) -> tuple[int, int, str]:
    m = _CODE.match(code or "")
    return (0, int(m.group(1)), "") if m else (1, 0, code)


def build_sources(
    profile: WorkspaceProfile,
    sources: Sequence[SourceRecord],
    articles: Iterable[ArticleRecord] = (),
) -> RepairedTable:
    """The Sources table: ``Sources`` (evidence) and ``Job pages`` (postings the run read). One
    row per canonical URL; a source with no web address is dropped and logged. ``articles`` are
    accepted so a read-only page can be created for an article the register never listed."""
    exclusions: list[Exclusion] = []
    groups: dict[str, list[SourceRecord]] = {}
    for rec in sources:
        canon = _valid_url(rec.url)
        if canon is None:
            reason = "no source URL" if not (rec.url or "").strip() else "not a web address"
            exclusions.append(Exclusion("sources", rec.code or rec.url or "", reason, rec.url))
            continue
        groups.setdefault(canon, []).append(rec)
    for art in articles:
        canon = _valid_url(art.url)
        if canon and canon not in groups:
            groups[canon] = [
                SourceRecord(
                    code=art.source_code or "",
                    url=art.url,
                    title=art.title,
                    publisher=art.publisher,
                    published=art.published
                    if art.published_kind in ("page", "url", "filing")
                    else None,
                    kind=art.source_type,
                    collected=art.collected,
                    read_only=not art.source_code,
                )
            ]

    evidence: list[tuple[tuple[int, int, str], str, dict[str, str]]] = []
    jobs: list[tuple[tuple[int, int, str], str, dict[str, str]]] = []
    aliases: dict[str, str] = {}
    duplicates = 0
    for canon, group in groups.items():
        rec = _merge(group)
        kept = norm_code(rec.code) if _is_real_code(rec.code) else READ
        for other in group:
            code = norm_code(other.code)
            if _is_real_code(other.code) and code != kept:
                aliases[code] = kept
                exclusions.append(
                    Exclusion("sources", code, "duplicate URL merged", f"{canon} -> {kept}")
                )
        duplicates += len(group) - 1
        row = {
            "Code": rec.code.strip() if _is_real_code(rec.code) else READ,
            "Title": _clean_title(rec, canon) or core.NOT_FOUND,
            "Publisher": publisher_for(canon, profile, rec.publisher),
            "Published": _published_cell(rec),
            "Source type": source_type_for(canon, profile, kind=rec.kind),
            "Link": canon,
            "Used in (deliverable + section)": "; ".join(rec.used_in) or core.NOT_APPLICABLE,
            "Collected": (
                rec.collected.date().isoformat()
                if rec.collected
                else core.not_collected("collection time not recorded")
            ),
        }
        item = (_code_key(kept if kept != READ else ""), canon, row)
        if row["Code"] == READ and is_job_page(canon, profile):
            jobs.append(item)
        else:
            evidence.append(item)

    def ordered(
        items: list[tuple[tuple[int, int, str], str, dict[str, str]]],
    ) -> list[dict[str, str]]:
        return [r for _, _, r in sorted(items, key=lambda i: (i[0], i[1]))]

    sheets = [
        Sheet("Sources", core.SOURCES_COLUMNS, ordered(evidence)),
        Sheet("Job pages", core.SOURCES_COLUMNS, ordered(jobs)),
    ]
    stats = {
        "sources_in": len(sources),
        "sources": len(evidence),
        "job_pages": len(jobs),
        "duplicates_removed": duplicates,
        "dropped": len(exclusions) - len(aliases),
        "unknown_published": sum(
            1 for s in sheets for r in s.rows if r["Published"] == core.UNKNOWN
        ),
        "code_aliases": aliases,
    }
    return RepairedTable("sources", "Sources", sheets, exclusions, stats)


def resolve_citations(codes: Iterable[str], sources_table: RepairedTable) -> list[str]:
    """The cited codes (``S1`` and ``S-0001`` are the same) that do NOT resolve to a row on any
    sheet of the sources table, as normalised codes, sorted. A code merged into another row with
    the same URL still resolves."""
    known = {norm_code(r.get("Code", "")) for s in sources_table.sheets for r in s.rows}
    known |= set(sources_table.stats.get("code_aliases", {}))
    missing = {norm_code(c) for c in codes if c and c.strip()} - known
    return sorted(missing, key=_code_key)
