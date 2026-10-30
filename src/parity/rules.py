"""The pure rules of the deliverables: platform matching and the platform sentences of a
posting, its class, its entity tag, the people level, lane, priority, former SI employers and
exclusions, the Platform Ranking and the tracker sort and tally. No I/O: the platform dictionary
and the entity rules come in as data, every function returns plain values.

The rules are a port of the reference workspace's build scripts; the reference numbers they
reproduce are asserted in ``tests/parity/test_rules.py``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from parity.platforms import LanePattern, Platforms
from parity.text import clean_posting

MAX_SENTENCES = 4
MAX_PLATFORM_SENTENCES = 3000
MAX_DESCRIPTION = 32000
TITLE_ONLY = "(named in the title only)"
CLASSES = ("Build role", "Admin and owner role", "Working tool", "User role")
CLASS_KEYS = {
    "Build role": "build",
    "Admin and owner role": "admin",
    "Working tool": "tool",
    "User role": "user",
}
FIELD_SERVICE_WHAT = (
    "Field service role that documents issues and resolutions in Salesforce Field Service "
    "Lightning."
)
_USAGE = re.compile(
    r"experience|proficien|knowledge|familiar|working with|using|utiliz|document|enter|update"
    r"|maintain|input|record|log|skills|transactions|data entr|administ|manage|maintain"
)
_SPACES = re.compile(r"\s+")


# ---------------------------------------------------------------- postings
def platform_sentences(description: str, pattern: str, flags: int) -> list[str]:
    """Up to four unique sentences of ``description`` in which ``pattern`` matches. A sentence
    runs from the character after the previous full stop or asterisk to the next full stop
    (the reference's boundary rule), on text with every run of whitespace collapsed."""
    text = _SPACES.sub(" ", description or "")
    out: list[str] = []
    for m in re.finditer(pattern, text, flags):
        start = max(text.rfind(".", 0, m.start()), text.rfind("*", 0, m.start())) + 1
        end = text.find(".", m.end())
        end = len(text) if end < 0 else end + 1
        sentence = text[start:end].strip()
        if sentence not in out:
            out.append(sentence)
        if len(out) >= MAX_SENTENCES:
            break
    return out


def match_platforms(
    title: str,
    description: str,
    platforms: Platforms,
    skip: Collection[str] = (),
) -> dict[str, list[str]]:
    """The lane platforms named in a posting, in dictionary order, each with its platform
    sentences (or the title-only marker). ``skip`` names platforms found false on reading."""
    text = f"{title or ''} {description or ''}"
    found: dict[str, list[str]] = {}
    for lane in platforms.lanes:
        if lane.name in skip or not re.search(lane.pattern, text, lane.flags):
            continue
        if re.search(lane.pattern, description or "", lane.flags):
            found[lane.name] = platform_sentences(description, lane.pattern, lane.flags)
        else:
            found[lane.name] = [TITLE_ONLY]
    return found


def join_sentences(found: Mapping[str, Sequence[str]]) -> str:
    """The Platform sentences cell: ``Platform: s1 | s2 || Platform2: ...``, cleaned and cut at
    3,000 characters."""
    joined = " || ".join(f"{name}: {' | '.join(sentences)}" for name, sentences in found.items())
    return clean_posting(joined)[:MAX_PLATFORM_SENTENCES]


def classify(
    decision: tuple[str, str] | None, found: Mapping[str, Sequence[str]]
) -> tuple[str, str]:
    """The class of a posting and what it says: the analyst's decision first; else Working tool
    for a Field Service Lightning role; else Working tool when the platform sentences carry a
    usage verb; else User role."""
    if decision:
        return decision[0], decision[1]
    text = " ".join(" ".join(s) for s in found.values()).lower()
    if "field service lightning" in text:
        return "Working tool", FIELD_SERVICE_WHAT
    if _USAGE.search(text):
        return "Working tool", ""
    return "User role", ""


@dataclass(frozen=True)
class EntityRule:
    """One entity label: the account's own positions (no patterns, the default) or a business
    tagged by wording, optionally only for postings first seen before a date."""

    label: str
    counted_as_account: bool
    patterns: tuple[str, ...] = ()
    before: str | None = None


def tag_entity(title: str, description: str, first_seen: str, rules: Sequence[EntityRule]) -> str:
    """The entity label of a posting: the first rule whose pattern matches the title or
    description (case sensitively; a dated rule only when the posting was first seen before
    its date), else the first rule without patterns, else the first rule."""
    text = f"{title or ''} {description or ''}"
    for rule in rules:
        if not rule.patterns:
            continue
        if rule.before and not (first_seen or "") < rule.before:
            continue
        if any(re.search(p, text) for p in rule.patterns):
            return rule.label
    for rule in rules:
        if not rule.patterns:
            return rule.label
    return rules[0].label


def jobs_counts(
    rows: Iterable[Mapping[str, Any]], lanes: Sequence[str], counted: Collection[str]
) -> dict[str, dict[str, int]]:
    """Per lane platform: total, open and class counts over the account's rows (``entity`` in
    ``counted``), the Summary numbers."""
    account = [r for r in rows if r.get("entity") in counted]
    out: dict[str, dict[str, int]] = {}
    for lane in lanes:
        hit = [r for r in account if lane in (r.get("platforms") or ())]
        counts = {"total": len(hit), "open": sum(1 for r in hit if r.get("status") == "Open")}
        for cls, key in CLASS_KEYS.items():
            counts[key] = sum(1 for r in hit if r.get("cls") == cls)
        out[lane] = counts
    return out


# ---------------------------------------------------------------- people
LEVELS: tuple[str, ...] = (
    "C-level, EVP and SVP",
    "VP",
    "Senior Director",
    "Director",
    "Associate Director",
    "Head",
    "Senior Manager",
    "Manager",
    "Lead, owner, specialist or developer",
)
SENIOR_MAX_LEVEL = 7
_LEVEL_RULES: tuple[tuple[int, str], ...] = (
    (1, r"\bsvp\b|\bevp\b|chief |\bcio\b|\bcto\b|\bceo\b|\bcfo\b|senior vice|executive vice"),
    (2, r"vice president|\bvp\b"),
    (3, r"senior director|sr\.? director|sr director|executive director"),
    (5, r"associate director|assoc\.? dir|assoc dir"),
    (4, r"director|\bdir\b"),
    (6, r"\bhead\b|\bglobal lead\b"),
    (
        7,
        r"senior manager|sr\.? manager|sr manager|senior it manager|senior program manager"
        r"|senior product manager|senior project manager|senior engineering manager"
        r"|sr\.? engineering manager|senior technical product manager",
    ),
    (8, r"manager|\bmgr\b"),
)
PEOPLE_LANES: tuple[str, ...] = (
    "Salesforce and commercial",
    "Salesforce (MuleSoft and integration)",
    "ServiceNow and shared services",
    "SAP and ERP",
    "Veeva and life sciences systems",
    "Analytics, data and AI",
    "Technology, digital and IT leadership",
    "Business leadership and other",
)
_LANE_RULES: tuple[tuple[str, str], ...] = (
    (
        PEOPLE_LANES[0],
        r"salesforce|sfdc|\bcrm\b|commercial (technology|systems|excellence|operations"
        r"|effectiveness)|sales (technology|operations|enablement|effectiveness)"
        r"|revenue (operations|technology)|customer (engagement|experience)|cpq|e-?commerce"
        r"|digital commerce|marketing (technology|automation|operations)",
    ),
    (
        PEOPLE_LANES[1],
        r"mulesoft|middleware|\bapi\b|enterprise integration|solution integration"
        r"|technical integration|integration (engineer|architect|platform|developer|lead"
        r"|engineering)",
    ),
    (
        PEOPLE_LANES[2],
        r"servicenow|\bitsm\b|service management|shared services|global business services"
        r"|\bgbs\b|service delivery|hr (technology|systems|service)|workday|hris",
    ),
    (
        PEOPLE_LANES[4],
        r"\bveeva\b|\bvault\b|medical affairs systems|regulatory systems|quality systems"
        r"|clinical systems|promomats|\bmlr\b",
    ),
    (
        PEOPLE_LANES[3],
        r"\bsap\b|s/4|\berp\b|master data|\bmdm\b|order management|order to cash"
        r"|supply chain (systems|technology|digital)|plm|\babap\b|basis",
    ),
    (
        PEOPLE_LANES[5],
        r"tableau|analytics|insights|visualization|\bbi\b|business intelligence"
        r"|data (product|engineering|science|scientist|platform|governance|architect"
        r"|management|strategy|analytics)|\bai\b|machine learning|artificial intelligence"
        r"|decision science|data and|data &",
    ),
    (
        PEOPLE_LANES[6],
        r"digital|technology|information|\bit\b|architect|software|cloud|devops"
        r"|infrastructure|enterprise applications|applications|platform|automation|cyber",
    ),
)
SECURITY = re.compile(r"security|cyber|it compliance|\baudit\b|privacy officer", re.IGNORECASE)
A_PAT = re.compile(
    r"(?x)salesforce|sfdc|\bcrm\b|mulesoft|servicenow|\bsap\b|s/4|\berp\b|master\ data|\bmdm\b"
    r"|tableau|\bveeva\b|\bvault\b|chief\ information|chief\ digital|chief\ technology"
    r"|chief\ data|vp\ of\ it|vp,?\ it|vp\ information"
    r"|vice\ president,?\ (it|information|technology|digital|data|analytics|enterprise)"
    r"|head\ of\ it|commercial\ (technology|systems)|sales\ technology"
    r"|revenue\ (operations|technology)|enterprise\ (applications|architecture|data|ai)"
    r"|data\ (engineering|platform|governance|architecture|and\ ai|&\ ai)|analytics"
    r"|digital\ (transformation|solutions|technology|platforms|excellence)"
    r"|enterprise\ integration|solution\ integration|api\ architect|middleware"
    r"|shared\ services|global\ business\ services|\bgbs\b|service\ delivery"
    r"|quality\ systems|clinical\ systems|regulatory\ systems|e-?commerce|cpq|order\ to\ cash"
    r"|supply\ chain\ (systems|technology|digital)|technology\ &\ global\ services"
    r"|technology\ and\ global\ services|\btgs\b|\bai\b|global\ business\ solutions"
    r"|revenue\ officer|supply\ chain\ officer|sales\ operations\ &\ effectiveness"
    r"|sales\ operations\ and\ effectiveness|direct\ to\ consumer"
)
C_PAT = re.compile(
    r"sales (rep|representative|specialist|consultant|executive)|territory|account manager"
    r"|key account|clinical (specialist|manager|consultant)|marketing manager"
    r"|product manager, (?!digital)|brand|scientist|r&d|research"
    r"|engineering manager(?!.*(software|platform|data|salesforce))|field service|plant"
    r"|manufacturing|quality (engineer|assurance)(?! systems)|regulatory affairs(?! systems)"
    r"|medical (director|affairs)(?! systems)|finance(?! systems)|accounting|tax|treasury"
    r"|legal|counsel|communications|public (affairs|relations)|government affairs|investor"
    r"|executive assistant|talent|recruit|learning|payroll|benefits|hr business partner"
    r"|human resources(?! (technology|systems))|\bemea\b.*(sales|marketing)"
    r"|general manager(?!.*(digital|technology))|^vp/gm|retired|emeritus|enduser|end user"
)
_C_KEEP = re.compile(r"salesforce|sfdc|servicenow|\bsap\b|veeva|master data|crm")
_B_PAT = re.compile(
    r"m&a|mergers|business integration|commercial integration|channel integration"
    r"|product integration|process integration|integration management office"
    r"|integration & change|integration and pricing|\bhai\b"
)
_C_ALWAYS = re.compile(r"executive assistant|emeritus|retired", re.IGNORECASE)
_NAMED_A = re.compile(
    r"chief information|business solutions|revenue officer|supply chain officer|servicenow"
    r"|direct to consumer",
    re.IGNORECASE,
)
LANEKW = re.compile(
    r"salesforce|sfdc|\bcrm\b|mulesoft|servicenow|\bsap\b|s/4|\berp\b|master data|\bmdm\b"
    r"|tableau|\bveeva\b|\bvault\b|workday|\babap\b|basis|hana|successfactors|ariba|concur"
    r"|power bi|snowflake|azure|aws\b|analytics|data engineer|integration|commerce cloud"
    r"|marketing cloud|service cloud|cpq|conga|pega|celonis|informatica|collibra",
    re.IGNORECASE,
)
SI: tuple[tuple[str, str], ...] = (
    ("Accenture", r"accenture"),
    ("Deloitte", r"deloitte"),
    ("PwC", r"\bpwc\b|pricewaterhouse"),
    ("EY", r"\bey\b|ernst"),
    ("KPMG", r"kpmg"),
    ("Capgemini", r"capgemini"),
    ("Cognizant", r"cognizant"),
    ("Infosys", r"infosys"),
    ("TCS", r"\btcs\b|tata consultancy"),
    ("Wipro", r"wipro"),
    ("HCL", r"\bhcl"),
    ("IBM", r"\bibm\b"),
    ("McKinsey", r"mckinsey"),
    ("BCG", r"\bbcg\b|boston consulting"),
    ("Bain", r"\bbain\b"),
    ("Tech Mahindra", r"tech mahindra"),
    ("LTIMindtree", r"mindtree|\blti\b|larsen"),
    ("Genpact", r"genpact"),
    ("DXC", r"\bdxc\b|hewlett packard enterprise services|\bcsc\b"),
    ("Slalom", r"slalom"),
    ("Persistent Systems", r"persistent systems"),
    ("CitiusTech", r"citiustech"),
    ("Virtusa", r"virtusa"),
    ("Hexaware", r"hexaware"),
    ("NTT Data", r"ntt data"),
    ("Mphasis", r"mphasis"),
    ("EPAM", r"\bepam\b"),
    ("Globant", r"globant"),
)


def level(title: str) -> tuple[int, str]:
    """The level of a title: 1 to 9 and its label (``LEVELS``)."""
    text = (title or "").lower()
    for number, pattern in _LEVEL_RULES:
        if re.search(pattern, text):
            return number, LEVELS[number - 1]
    return 9, LEVELS[8]


def is_senior(level_number: int) -> bool:
    """The seniority rule: Director and above, Head, and Senior Manager (levels 1 to 7)."""
    return level_number <= SENIOR_MAX_LEVEL


def lane(title: str) -> str:
    """The people lane of a title (``PEOPLE_LANES``)."""
    text = (title or "").lower()
    for label, pattern in _LANE_RULES:
        if re.search(pattern, text):
            return label
    return PEOPLE_LANES[-1]


def priority(title: str) -> str:
    """The priority of a title: C for a title outside the lanes (unless it names a lane
    platform), B for an integration or business role, A for a platform, data or program owner
    or the executive a lane reports to, else B."""
    text = (title or "").lower()
    if C_PAT.search(text) and not _C_KEEP.search(text):
        return "C"
    if _B_PAT.search(text):
        return "B"
    if A_PAT.search(text):
        return "A"
    return "B"


def priority_named(title: str) -> str:
    """The priority of a person named in research (no search record): A for an executive or
    platform owner title, else B."""
    return "A" if _NAMED_A.search(title or "") else "B"


def always_c(title: str) -> bool:
    """A retired, emeritus or executive assistant title is always C."""
    return bool(_C_ALWAYS.search(title or ""))


def is_security(title: str, tagged: bool = False) -> bool:
    """The security, IT compliance and IT audit exclusion."""
    return tagged or bool(SECURITY.search(title or ""))


def names_platform(title: str) -> bool:
    """Whether a title names a platform or technology (``LANEKW``): a person below the
    seniority rule is kept only then."""
    return bool(LANEKW.search(title or ""))


def si_employers(profile: Mapping[str, Any]) -> str:
    """Former SI and consulting employers from a profile's experiences: ``Firm (from to to)``
    (``to date`` for a current role) joined by ``; ``, one entry per firm, in the order first
    seen."""
    out: list[tuple[str, str]] = []
    for exp in profile.get("experiences") or []:
        company = exp.get("company") or ""
        for name, pattern in SI:
            if not re.search(pattern, company, re.IGNORECASE):
                continue
            years = ""
            if exp.get("startDate"):
                end = (exp.get("endDate") or "")[:4] or "date"
                years = f"{(exp.get('startDate') or '')[:4]} to {end}"
            if name not in [o[0] for o in out]:
                out.append((name, years))
    return "; ".join(f"{n} ({y})" if y else n for n, y in out)


def people_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    """The People sheet order: level, priority, lane, name."""
    return (
        int(row["level"]),
        {"A": 0, "B": 1, "C": 2}[str(row["priority"])],
        str(row.get("lane") or ""),
        str(row.get("name") or ""),
    )


# ---------------------------------------------------------------- ranking
@dataclass(frozen=True)
class RankRow:
    """One ranked platform of the Platform Ranking sheet."""

    platform: str
    category: str
    lane: str
    count: int
    open: int
    last_seen: str
    in_stack: str
    checked: str


@dataclass(frozen=True)
class SeenOnce:
    """A platform named in one posting only."""

    platform: str
    category: str
    lane: str


@dataclass(frozen=True)
class Ranking:
    """The recomputed ranking."""

    base: int
    rows: list[RankRow] = field(default_factory=list)
    seen_once: list[SeenOnce] = field(default_factory=list)


def rank_letter(count: int, base: int) -> str:
    """A: share 15 percent or more and 5 or more postings; B: 5 percent and 3; C: 2; else
    blank (the sheet's formula, computed in code)."""
    share = count / base if base else 0.0
    if share >= 0.15 and count >= 5:
        return "A"
    if share >= 0.05 and count >= 3:
        return "B"
    return "C" if count >= 2 else ""


def rank_rows(
    jobs: Iterable[Mapping[str, Any]],
    platforms: Platforms,
    drops: Collection[tuple[str, str]] = (),
    checked: Collection[str] = (),
    stack_names: Sequence[str] | None = None,
) -> Ranking:
    """Rank the dictionary platforms by distinct postings naming them (``t`` and ``d`` of each
    job; ``status`` closed or not; ``l`` or ``f`` for the last seen date; ``url``). ``drops``
    are (platform, url) pairs found false on reading; ``checked`` the platforms confirmed by
    reading; ``stack_names`` the confirmed stack (None when not pulled)."""
    dropped = set(drops)
    hits: dict[str, list[Mapping[str, Any]]] = {p.name: [] for p in platforms.ranking}
    base: set[str] = set()
    for job in jobs:
        text = f"{job.get('t') or ''} {job.get('d') or ''}"
        url = str(job.get("url") or "")
        for entry in platforms.ranking:
            if entry.regex.search(text) and (entry.name, url) not in dropped:
                hits[entry.name].append(job)
                base.add(url)
    stack = " | ".join(stack_names) if stack_names else ""
    rows: list[RankRow] = []
    once: list[SeenOnce] = []
    for entry in platforms.ranking:
        found = hits[entry.name]
        if not found:
            continue
        lane_label = entry.lane or "Outside the lanes"
        if len(found) < 2:
            once.append(SeenOnce(entry.name, entry.category, lane_label))
            continue
        if stack_names is None:
            in_stack = "Not pulled"
        else:
            in_stack = "Yes" if stack and entry.regex.search(stack) else "No"
        rows.append(
            RankRow(
                platform=entry.name,
                category=entry.category,
                lane=lane_label,
                count=len(found),
                open=sum(1 for j in found if j.get("status") != "closed"),
                last_seen=max(str(j.get("l") or j.get("f") or "") for j in found),
                in_stack=in_stack,
                checked="Yes" if entry.name in checked else "No",
            )
        )
    rows.sort(key=lambda r: r.count, reverse=True)
    return Ranking(len(base), rows, once)


# ---------------------------------------------------------------- tracker
_TRACKER_ORDER = {"High": 0, "Medium": 1, "Optional": 2}


def sort_tracker_p2(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """Pass 2 rows by priority (High, Medium, Optional), then rows whose Needs is Nothing
    first; a stable sort, as the reference prints them."""
    return sorted(
        (list(r) for r in rows),
        key=lambda r: (_TRACKER_ORDER.get(r[2], 3), 0 if r[3] == "Nothing" else 1),
    )


def tracker_tally(p1: Sequence[Sequence[str]], p2: Sequence[Sequence[str]]) -> dict[str, int]:
    """Status counts over both tracker tables, in the order the statuses first appear."""
    tally: Counter[str] = Counter()
    for row in (*p1, *p2):
        tally[row[1]] += 1
    return dict(tally)


def lane_pattern(platforms: Platforms, name: str) -> LanePattern:
    """The lane pattern of ``name`` (raises when the dictionary lacks it)."""
    for lane_entry in platforms.lanes:
        if lane_entry.name == name:
            return lane_entry
    raise KeyError(name)
