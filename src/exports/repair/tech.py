"""Tech stack and teams of the repaired export (spec table 3): three sheets built from the unique
postings (``jobs.build_jobs``) and the website / feed detections.

* ``Platform Ranking``: base = unique account postings, inside the window, naming any dictionary
  platform (the ranking dictionary of ``config/parity/platforms.yaml``, plus any lane platform the
  postings name). Rank A: share >= 15 percent and >= 5 postings; B: >= 5 percent and >= 3; C: >= 2
  postings; exactly one posting: ``Seen once``.
* ``Confirmed stack``: one row per normalised technology, postings-derived and detected technologies
  merged; ``Cross check`` Both / Postings only / Detection only on every row.
* ``Platform teams``: the 25 teams (team or function, plus location) with most account postings
  that name a lane platform.

Formula cell convention (read by the XLSX writer; ``core.Sheet`` is not changed): a row of the
ranking sheet keeps the computed value text under the normal column key (what CSV prints) and the
spreadsheet formula under a parallel key ``'__formula__:<Column>'``, for example
``row['__formula__:Share of base'] == '=E2/1904'``. The formulas refer to the sheet's own cells
(header in row 1, first data row in row 2) and carry the base as a literal, so they stay valid when
the sheet is opened on its own. ``Sheet.cell_rows`` ignores these keys, so CSV output is unchanged.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from exports.repair.core import (
    NOT_APPLICABLE,
    NOT_FOUND,
    RANKING_COLUMNS,
    STACK_COLUMNS,
    TEAM_COLUMNS,
    UNKNOWN,
    Exclusion,
    RepairedTable,
    Sheet,
    WorkspaceProfile,
    iso,
)
from exports.repair.entity import account_registrables
from exports.repair.jobs import ResolvedPosting, get_platforms, out_of_scope_names
from exports.repair.keys import fold, host_of, registrable
from exports.repair.records import TechRecord
from parity.rules import rank_letter

FORMULA_PREFIX = "__formula__:"
MAX_TECH_CHARS = 40
MAX_TECH_WORDS = 5
TOP_TEAMS = 25
SEEN_ONCE = "Seen once"
OUTSIDE_LANES = "Outside the lanes"

# Generic cloud / vendor naming: canonical name -> variants (matched after the generic reduction
# below). Nothing here is specific to an account.
ALIASES: dict[str, tuple[str, ...]] = {
    "Amazon Web Services": ("aws", "amazon aws", "amazon web services", "amazon cloud"),
    "Microsoft Azure": ("azure", "microsoft azure", "azure cloud", "ms azure"),
    "Google Cloud": ("gcp", "google cloud", "google cloud platform"),
    "Power BI": ("power bi", "powerbi", "microsoft power bi"),
    "Azure Machine Learning": ("azure ml", "azure machine learning", "azure ml studio"),
    "Amazon Athena": ("athena", "aws athena", "amazon athena"),
    "Amazon SageMaker": ("sagemaker", "aws sagemaker", "amazon sagemaker"),
    "Amazon Redshift": ("redshift", "aws redshift", "amazon redshift"),
    "Amazon S3": ("s3", "aws s3", "amazon s3", "amazon simple storage service"),
    "Microsoft 365": ("microsoft 365", "office 365", "o365", "m365", "microsoft office 365"),
    "Microsoft Dynamics 365": ("dynamics 365", "d365", "microsoft dynamics 365", "ms dynamics 365"),
    "Google Analytics": ("google analytics", "ga4", "google analytics 4"),
    "Salesforce": ("salesforce", "salesforce crm", "salesforce.com", "sfdc"),
    "Microsoft SQL Server": ("sql server", "mssql", "ms sql server", "microsoft sql server"),
    "Amazon EC2": ("ec2", "aws ec2", "amazon ec2"),
}  # fmt: skip
_PREFIXES = frozenset({"ms", "microsoft", "amazon", "aws", "google"})
_SUFFIXES = frozenset({"platform", "suite", "software", "solution", "solutions", "tool", "tools"})
_TOKEN_MAP = {"ml": "machine learning"}
_VERBS = frozenset(
    {
        "is", "are", "was", "were", "been", "has", "have", "had", "uses", "using", "leverages",
        "leveraging", "provides", "providing", "enables", "enabling", "delivers", "delivering",
        "powers", "powered", "runs", "running", "offers", "offering", "supports", "supporting",
        "helps", "helping", "includes", "including", "announced", "announces", "launched",
        "launches", "adopted", "adopts", "migrated", "migrating", "implemented", "implementing",
        "deployed", "deploying", "selected", "chose", "partnered", "expands", "expanded", "will",
        "would", "can", "could", "should", "may", "our", "we", "their", "your",
    }
)  # fmt: skip
_NUMBER_LEAD = re.compile(r"^\d[\d,.]*\+?%?\s+\S+")
_ISO_DAY = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})")
_WORDS = re.compile(r"[a-z0-9+#./]+")


# ---------------------------------------------------------------- technology names
def _reduce(name: str) -> str:
    """The generic reduction of a technology name: folded, vendor prefix and generic suffix
    dropped, abbreviations spelled out."""
    tokens = _WORDS.findall(fold(name).replace("&", " and "))
    tokens = [_TOKEN_MAP.get(t, t) for t in tokens]
    # LOOP(strip name prefixes): cap=the words of a technology name; exit=no prefix word left
    while len(tokens) > 1 and tokens[0] in _PREFIXES:
        tokens = tokens[1:]
    # LOOP(strip name suffixes): cap=the words of a technology name; exit=no suffix word left
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(" ".join(tokens).split())


_ALIAS_KEYS: dict[str, str] = {
    _reduce(variant): canonical
    for canonical, variants in ALIASES.items()
    for variant in (canonical, *variants)
}


def tech_key(name: str) -> tuple[str, str]:
    """``(key, display)`` of a technology: the alias table gives the canonical name, otherwise the
    reduced name is the key and the name itself the display."""
    reduced = _reduce(name)
    canonical = _ALIAS_KEYS.get(reduced)
    if canonical:
        return fold(canonical), canonical
    return reduced, name.strip()


def reject_reason(technology: str) -> str:
    """Why ``technology`` is not a technology name (a sentence, a verb phrase, a statistic), or
    ``""`` when it is acceptable."""
    text = technology.strip()
    if not text:
        return "empty"
    if len(text) > MAX_TECH_CHARS:
        return f"longer than {MAX_TECH_CHARS} characters"
    if text.endswith("."):
        return "ends with a full stop"
    words = text.split()
    if len(words) > MAX_TECH_WORDS:
        return f"more than {MAX_TECH_WORDS} words"
    lead = _NUMBER_LEAD.match(text)
    if lead and (re.search(r"[,+%]", words[0]) or len(words) >= 3):
        return "starts like a statistic"
    if _VERBS & set(_WORDS.findall(fold(text))):
        return "contains a verb phrase"
    return ""


def _day(text: str) -> date | None:
    m = _ISO_DAY.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------- accumulation
@dataclass
class _Tech:
    names: Counter[str] = field(default_factory=Counter)
    posting_ids: set[str] = field(default_factory=set)
    detections: int = 0
    firsts: list[date] = field(default_factory=list)
    lasts: list[date] = field(default_factory=list)
    post_urls: list[tuple[date, str]] = field(default_factory=list)
    det_urls: list[tuple[date, str]] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    dict_category: str = ""
    dict_function: str = ""


def _ranking_hits(
    profile: WorkspaceProfile, account: list[ResolvedPosting]
) -> tuple[list[tuple[str, str, str, list[ResolvedPosting]]], set[str]]:
    """``(name, category, lane, postings)`` for every dictionary platform the account postings
    name, and the ids of the postings that name any platform (the base)."""
    platforms = get_platforms(profile)
    oos = out_of_scope_names(profile)
    entries = [
        (e.name, e.category, e.lane, e.regex) for e in platforms.ranking if fold(e.name) not in oos
    ]
    ranked = {fold(e[0]) for e in entries}
    hits: dict[str, list[ResolvedPosting]] = {e[0]: [] for e in entries}
    extra: dict[str, list[ResolvedPosting]] = {}
    base: set[str] = set()
    for p in account:
        text = f"{p.title} {p.description}"
        hit = False
        for name, _cat, _lane, rx in entries:
            if name in p.platforms or rx.search(text):
                hits[name].append(p)
                hit = True
        for name in p.platforms:
            if fold(name) not in ranked:
                extra.setdefault(name, []).append(p)
                hit = True
        if hit:
            base.add(p.posting_id)
    out = [(n, c, ln, hits[n]) for n, c, ln, _rx in entries if hits[n]]
    out += [(n, NOT_FOUND, n, ps) for n, ps in extra.items()]
    return out, base


def _add_postings(
    stack: dict[str, _Tech], hits: list[tuple[str, str, str, list[ResolvedPosting]]]
) -> None:
    for name, category, lane, posts in hits:
        key, display = tech_key(name)
        t = stack.setdefault(key, _Tech())
        t.names[display] += len(posts)
        t.posting_ids |= {p.posting_id for p in posts}
        t.dict_category = t.dict_category or category
        t.dict_function = t.dict_function or lane
        for p in posts:
            if p.first_seen:
                t.firsts.append(p.first_seen)
            if p.last_seen:
                t.lasts.append(p.last_seen)
            t.post_urls.append((p.last_seen or p.first_seen or date.min, p.url))


def _add_detections(
    profile: WorkspaceProfile,
    stack: dict[str, _Tech],
    records: list[TechRecord],
    exclusions: list[Exclusion],
) -> None:
    allowed = account_registrables(profile)
    for rec in sorted(records, key=lambda r: (fold(r.technology), r.source_url)):
        tech = re.sub(r"\s+", " ", rec.technology or "").strip()
        why = reject_reason(tech)
        if why:
            exclusions.append(
                Exclusion("tech", tech[:60] or "(empty)", f"technology value rejected: {why}")
            )
            continue
        url = (rec.source_url or "").strip()
        if not url:
            exclusions.append(Exclusion("tech", tech, "no source URL"))
            continue
        domain = (rec.detected_domain or "").strip() or host_of(url)
        reg = registrable(host_of(domain) or domain.lower().strip("."))
        if reg not in allowed:
            exclusions.append(
                Exclusion("tech", tech, "detection domain is not the account's", domain)
            )
            continue
        key, display = tech_key(tech)
        t = stack.setdefault(key, _Tech())
        weight = max(rec.detections, 1)
        t.names[display] += weight
        t.detections += weight
        first, last = _day(rec.first_seen), _day(rec.last_seen)
        if first:
            t.firsts.append(first)
        if last:
            t.lasts.append(last)
        t.det_urls.append((last or first or date.min, url))
        t.domains.append(host_of(domain) or domain.lower())
        if rec.category.strip():
            t.categories.append(rec.category.strip())
        if rec.business_function.strip():
            t.functions.append(rec.business_function.strip())


def _display(names: Counter[str], key: str) -> str:
    canonical = next((c for c in ALIASES if fold(c) == key), "")
    if canonical:
        return canonical
    return sorted(names, key=lambda n: (-names[n], -len(n), n))[0]


def _stack_rows(stack: dict[str, _Tech]) -> list[dict[str, str]]:
    rows: list[tuple[int, str, dict[str, str]]] = []
    for key, t in stack.items():
        postings = len(t.posting_ids)
        both = postings > 0 and t.detections > 0
        if t.detections:
            url = sorted(t.det_urls, key=lambda x: (-x[0].toordinal(), x[1]))[0][1]
            source_type = "Website detection"
            domain = sorted(t.domains)[0]
        else:
            url = sorted(t.post_urls, key=lambda x: (-x[0].toordinal(), x[1]))[0][1]
            source_type = "Job postings"
            domain = NOT_APPLICABLE
        name = _display(t.names, key)
        rows.append(
            (
                postings + t.detections,
                fold(name),
                {
                    "Business function": (t.functions or [t.dict_function])[0] or NOT_FOUND,
                    "Category": (t.categories or [t.dict_category])[0] or NOT_FOUND,
                    "Technology": name,
                    "Postings naming it": str(postings),
                    "Detections": str(t.detections),
                    "Cross check": "Both"
                    if both
                    else ("Postings only" if postings else "Detection only"),
                    "First seen": iso(min(t.firsts)) if t.firsts else UNKNOWN,
                    "Last seen": iso(max(t.lasts)) if t.lasts else UNKNOWN,
                    "Source URL": url,
                    "Source type": source_type,
                    "Detected domain": domain,
                },
            )
        )
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [r[2] for r in rows]


def _ranking_sheet(
    hits: list[tuple[str, str, str, list[ResolvedPosting]]],
    base: int,
    stack_keys: set[str],
) -> Sheet:
    sheet = Sheet("Platform Ranking", RANKING_COLUMNS)
    col = {name: chr(ord("A") + i) for i, name in enumerate(RANKING_COLUMNS)}
    count_col, share_col = col["Postings naming it"], col["Share of base"]
    for n, (name, category, lane, posts) in enumerate(
        sorted(hits, key=lambda h: (-len(h[3]), fold(h[0]))), start=2
    ):
        count = len(posts)
        share = count / base if base else 0.0
        letter = rank_letter(count, base) or (SEEN_ONCE if count == 1 else "")
        sheet.rows.append(
            {
                "Rank": letter,
                "Platform": name,
                "Category": category or NOT_FOUND,
                "Lane": lane or OUTSIDE_LANES,
                "Postings naming it": str(count),
                "Share of base": f"{share:.1%}",
                "Open now": str(sum(1 for p in posts if p.is_open)),
                "In confirmed stack": "Yes" if tech_key(name)[0] in stack_keys else "No",
                f"{FORMULA_PREFIX}Share of base": f"={count_col}{n}/{base}" if base else "=0",
                f"{FORMULA_PREFIX}Rank": (
                    f'=IF(AND({share_col}{n}>=0.15,{count_col}{n}>=5),"A",'
                    f'IF(AND({share_col}{n}>=0.05,{count_col}{n}>=3),"B",'
                    f'IF({count_col}{n}>=2,"C","{SEEN_ONCE}")))'
                ),
            }
        )
    return sheet


def _teams_sheet(account: list[ResolvedPosting]) -> tuple[Sheet, int]:
    groups: dict[tuple[str, str], list[ResolvedPosting]] = defaultdict(list)
    skipped = 0
    for p in account:
        if not p.platforms:
            continue
        team = p.team.strip() or p.function.strip()
        if not team:
            skipped += 1
            continue
        groups[(fold(team), fold(p.location))].append(p)
    ordered = sorted(groups.values(), key=lambda g: (-len(g), fold(g[0].team or g[0].function)))
    sheet = Sheet("Platform teams", TEAM_COLUMNS)
    for posts in ordered[:TOP_TEAMS]:
        first = posts[0]
        names = sorted({n for p in posts for n in p.platforms})
        sheet.rows.append(
            {
                "Team": first.team.strip() or first.function.strip(),
                "Function": first.function.strip() or NOT_FOUND,
                "Location": first.location.strip() or NOT_FOUND,
                "Postings": str(len(posts)),
                "Platforms": ", ".join(names),
            }
        )
    return sheet, skipped


def build_tech(
    profile: WorkspaceProfile,
    tech_records: list[TechRecord],
    resolved_postings: list[ResolvedPosting],
) -> RepairedTable:
    """The Tech stack and teams table (three sheets; see the module docstring)."""
    exclusions: list[Exclusion] = []
    account = [p for p in resolved_postings if p.counted]
    hits, base_ids = _ranking_hits(profile, account)
    stack: dict[str, _Tech] = {}
    _add_postings(stack, hits)
    _add_detections(profile, stack, tech_records, exclusions)
    stack_sheet = Sheet("Confirmed stack", STACK_COLUMNS, rows=_stack_rows(stack))
    detected = {k for k, t in stack.items() if t.detections}
    ranking = _ranking_sheet(hits, len(base_ids), detected)
    teams, no_team = _teams_sheet(account)
    cross = Counter(r["Cross check"] for r in stack_sheet.rows)
    notes = []
    if not base_ids:
        notes.append("No account posting inside the window names a dictionary platform.")
    if no_team:
        notes.append(f"{no_team} platform postings carry neither a team nor a function.")
    return RepairedTable(
        key="tech",
        title="Tech stack and teams",
        sheets=[ranking, stack_sheet, teams],
        exclusions=exclusions,
        stats={
            "ranking_base": len(base_ids),
            "ranking_rows": len(ranking.rows),
            "stack_rows": len(stack_sheet.rows),
            "cross_check": dict(cross),
            "team_rows": len(teams.rows),
            "postings_without_team": no_team,
            "detections_rejected": len(exclusions),
        },
        notes=notes,
    )


__all__ = [
    "ALIASES",
    "FORMULA_PREFIX",
    "build_tech",
    "reject_reason",
    "tech_key",
]
