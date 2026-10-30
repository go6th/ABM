"""The Signals table: dated, sourced, typed statements generated from the other repaired tables
(job postings, news and its facts, leadership) and from the signals the pipeline already stored.

Every pattern here is generic wording; nothing names an account, a person, an event or a platform
owner. A signal with no source at all is dropped and logged (spec rule 5)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from exports.repair import core
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.keys import canonical_url, fold
from exports.repair.records import SignalSeed

TABLE_KEY = "signals"
TABLE_TITLE = "Signals"
SPIKE_DAYS = 90
SPIKE_FACTOR = 2.0
SPIKE_MIN_POSTINGS = 5
CONFIRMED_MIN_POSTINGS = 3
PROGRAM_MIN_POSTINGS = 2
EVIDENCE_URLS_PER_SIGNAL = 3
STATEMENT_MAX_CHARS = 300

_SEPARATORS = re.compile(r"\s*[;,|]\s*")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
_CODE = re.compile(r"^([A-Za-z]*)(\d+)$")

# ---- generic wording per signal type (case-insensitive) -----------------------------------------
_ROLE_WORDS = (
    r"chief|officer|ceo|cfo|cio|cto|cro|coo|cmo|cdo|president|head of|director|vice president"
    r"|\bvp\b|chair(?:man|woman|person)?|general manager|managing director|board"
)
_LEADERSHIP_ACTION = re.compile(
    r"\b(appoint\w*|promot\w*|names?|named|hires?|hired|joins?|joined|succe(?:ed|eds|eded|ssion)"
    r"|successor|steps? down|stepped down|stepping down|retir\w*|resign\w*|depart\w*"
    r"|elect(?:s|ed)?|leaves?|left|transition\w*)\b",
    re.I,
)
_LEADERSHIP_ROLE = re.compile(rf"\b(?:{_ROLE_WORDS})\b", re.I)
_MNA = re.compile(
    r"\b(acqui(?:re|res|red|ring|sition|sitions)|divest\w*|separation|spin(?:s|ning)?[- ]?offs?"
    r"|spin(?:s|ning)? off|combin(?:e|es|ed|ing|ation)|merger|merg(?:e|es|ed|ing)"
    r"|definitive agreement|carve[- ]?outs?|joint venture|takeover|buyout)\b",
    re.I,
)
_REGULATORY = re.compile(
    r"\b(warning letters?|recall(?:s|ed)?|fda|ema|sec|enforcement|consent decrees?"
    r"|subpoenas?|regulator\w*|inspection finding\w*)\b",
    re.I,
)
_FUNDING = re.compile(
    r"\b(notes? (?:issue|issuance|offering|due)|senior notes|bond (?:issue|issuance|offering)"
    r"|offering|issuance|results? dates?|earnings (?:release|call|date)"
    r"|(?:first|second|third|fourth)[- ]quarter results|quarterly results|full[- ]year results"
    r"|guidance|dividends?|buy[- ]?backs?|share repurchase|repurchase program(?:me)?"
    r"|credit facility|financing|funding round)\b",
    re.I,
)
_PROGRAM = re.compile(
    r"\b(harmoni[sz]ation|migrat\w+|roll[- ]?outs?|transformation|implementation"
    r"|program(?:me)?s?|go[- ]live|moderni[sz]ation|re-?platform\w*)\b",
    re.I,
)
# the narrower wording used on a posting's platform sentences ("program" alone is job-speak)
_POSTING_PROGRAM = re.compile(
    r"\b(harmoni[sz]ation|migrat\w+|roll[- ]?outs?|transformation|implementation"
    r"|moderni[sz]ation|re-?platform\w*)\b",
    re.I,
)


def _leadership_pos(text: str) -> int | None:
    """Start of a leadership wording: an appointment/retirement verb with a role word."""
    if not _LEADERSHIP_ROLE.search(text):
        return None
    m = _LEADERSHIP_ACTION.search(text)
    return m.start() if m else None


def _regex_pos(rx: re.Pattern[str]) -> Callable[[str], int | None]:
    def find(text: str) -> int | None:
        m = rx.search(text)
        return m.start() if m else None

    return find


# priority order = SIGNAL_TYPES order for the news-wording types
_DETECTORS: tuple[tuple[str, Callable[[str], int | None]], ...] = (
    ("Leadership change", _leadership_pos),
    ("M&A / divestiture", _regex_pos(_MNA)),
    ("Regulatory event", _regex_pos(_REGULATORY)),
    ("Funding / financial", _regex_pos(_FUNDING)),
    ("Program or migration", _regex_pos(_PROGRAM)),
)


def classify_text(text: str) -> str | None:
    """The signal type a sentence is about, or None. When several wordings match, the one that
    starts first wins (ties go to the order of ``SIGNAL_TYPES``)."""
    best: tuple[int, int, str] | None = None
    for rank, (kind, find) in enumerate(_DETECTORS):
        pos = find(text or "")
        if pos is not None and (best is None or (pos, rank) < best[:2]):
            best = (pos, rank, kind)
    return best[2] if best else None


# ---- stored type words -> SIGNAL_TYPES ----------------------------------------------------------
_TYPE_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"leadership|exec(?:utive)? (?:change|move|hire)"), "Leadership change"),
    (re.compile(r"build[ -]?role|build[ -]?hiring"), "Build-role hiring"),
    (re.compile(r"hiring spike|platform hiring"), "Platform hiring spike"),
    (re.compile(r"\bm ?& ?a\b|merger|acquisition|divest|spin"), "M&A / divestiture"),
    (re.compile(r"regulat|compliance|recall|enforcement"), "Regulatory event"),
    (re.compile(r"financ|\bfunding\b|earnings|dividend|offering"), "Funding / financial"),
    (re.compile(r"program|migration|transformation|rollout"), "Program or migration"),
)


_FUNDING_MA = ("M&A / divestiture", "Funding / financial")


def map_signal_type(word: str, title: str = "") -> str | None:
    """Map a stored ``signal_type`` word onto ``SIGNAL_TYPES``; an ambiguous or unknown word
    (``funding_ma``, ``news_event``, ``other``) is decided by the title's wording. None: drop."""
    clean = re.sub(r"[_\-/]+", " ", (word or "").lower()).strip()
    for known in core.SIGNAL_TYPES:
        if clean == known.lower():
            return known
    if re.fullmatch(r"funding ?ma|funding m ?& ?a|funding and m ?& ?a", clean):
        kind = classify_text(title)
        return kind if kind in _FUNDING_MA else None
    for rx, kind in _TYPE_WORDS:
        if rx.search(clean):
            return kind
    return classify_text(title)


# ---- small helpers ------------------------------------------------------------------------------
def parse_day(text: str) -> date | None:
    """A date from an ISO-looking cell (``2026-03-04`` or a timestamp); None for anything else
    (including every placeholder)."""
    m = _ISO.match(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalise_grade(text: str) -> str:
    """Confirmed / Probable / Lead from any stored grade word (hypothesis and unknown: Lead)."""
    low = (text or "").strip().lower()
    if low.startswith("conf"):
        return "Confirmed"
    if low.startswith("prob"):
        return "Probable"
    return "Lead"


_GRADE_RANK = {"Confirmed": 0, "Probable": 1, "Lead": 2}


def _val(row: Mapping[str, str], column: str) -> str:
    """A real cell value: blank and every placeholder read as ''."""
    text = str(row.get(column, "") or "").strip()
    return "" if not text or core.is_placeholder(text) else text


def _split(text: str) -> list[str]:
    return [p.strip() for p in _SEPARATORS.split(text or "") if p.strip()]


def _sentence(text: str) -> str:
    """One clean sentence: whitespace collapsed, cut at the length ceiling, terminated."""
    clean = " ".join((text or "").split())
    if len(clean) > STATEMENT_MAX_CHARS:
        cut = clean[:STATEMENT_MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("; "))
        clean = cut[: end + 1] if end > STATEMENT_MAX_CHARS // 2 else cut.rstrip(" ,;:") + "..."
    return clean if clean[-1:] in ".!?" else clean + "."


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _code_key(code: str) -> tuple[str, int, str]:
    m = _CODE.match(code)
    return (m.group(1), int(m.group(2)), code) if m else ("~", 0, code)


def _sheet(tables: Mapping[str, RepairedTable], key: str, name: str) -> Sheet | None:
    table = tables.get(key)
    if table is None:
        return None
    return next((s for s in table.sheets if s.name == name), None)


@dataclass
class _Signal:
    text: str
    kind: str
    lane: str = core.NOT_APPLICABLE
    grade: str = "Lead"
    detected: date | None = None
    event: date | None = None
    codes: set[str] = field(default_factory=set)
    urls: list[str] = field(default_factory=list)

    def has_source(self) -> bool:
        return bool(self.codes or self.urls)

    def evidence(self) -> str:
        chunks = [", ".join(sorted(self.codes, key=_code_key))] if self.codes else []
        return "; ".join([*chunks, *self.urls])


def _norm_key(kind: str, text: str) -> tuple[str, str]:
    return kind, re.sub(r"[^a-z0-9]+", " ", fold(text)).strip()


class _Builder:
    """Collects candidate signals, merges duplicates and logs what is dropped."""

    def __init__(self, profile: WorkspaceProfile, tables: Mapping[str, RepairedTable]) -> None:
        self.profile = profile
        self.tables = tables
        self.found: dict[tuple[str, str], _Signal] = {}
        self.exclusions: list[Exclusion] = []
        self.dropped = 0
        self.skipped: list[str] = []
        self.code_by_url = self._source_codes()
        self.account_labels = self._account_labels()

    # -- sources ---------------------------------------------------------------------------------
    def _source_codes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        table = self.tables.get("sources")
        for sheet in table.sheets if table else ():
            if "Code" not in sheet.columns or "Link" not in sheet.columns:
                continue
            for row in sheet.rows:
                code, link = _val(row, "Code"), _val(row, "Link")
                if code and link and code.lower() != "read":
                    out.setdefault(canonical_url(link), code)
        return out

    def attach(self, sig: _Signal, urls: Sequence[str] = (), codes: Sequence[str] = ()) -> None:
        """Add evidence: a source code when the URL is in the register, else the URL itself."""
        for code in codes:
            if code.strip() and not core.is_placeholder(code):
                sig.codes.add(code.strip())
        for url in urls:
            link = (url or "").strip()
            if not link or core.is_placeholder(link):
                continue
            known = self.code_by_url.get(canonical_url(link))
            if known:
                sig.codes.add(known)
            elif link not in sig.urls:
                sig.urls.append(link)

    def _account_labels(self) -> set[str]:
        labels = {self.profile.label}
        labels.update(r.label for r in self.profile.entities if r.counts_as_account)
        return {fold(x) for x in labels if x}

    # -- emission --------------------------------------------------------------------------------
    def emit(self, sig: _Signal) -> None:
        if not sig.has_source():
            self.dropped += 1
            self.exclusions.append(Exclusion("signals", sig.text, "no source", sig.kind))
            return
        key = _norm_key(sig.kind, sig.text)
        old = self.found.get(key)
        if old is None:
            self.found[key] = sig
            return
        if _GRADE_RANK[sig.grade] < _GRADE_RANK[old.grade]:
            old.grade = sig.grade
        old.codes |= sig.codes
        old.urls.extend(u for u in sig.urls if u not in old.urls)
        if old.event is None or (sig.event and sig.event > old.event):
            old.event = sig.event or old.event
        if old.lane == core.NOT_APPLICABLE:
            old.lane = sig.lane

    # -- lanes -----------------------------------------------------------------------------------
    def lane_lookup(self) -> Callable[[str, Sequence[str]], str]:
        ranking: dict[str, str] = {}
        sheet = _sheet(self.tables, "tech", "Platform Ranking")
        for row in sheet.rows if sheet else ():
            platform, lane = _val(row, "Platform"), _val(row, "Lane")
            if platform and lane:
                ranking.setdefault(fold(platform), lane)
        dictionary = getattr(self.profile.platforms, "ranking", ()) or ()
        for entry in dictionary:
            name, lane = str(getattr(entry, "name", "")), str(getattr(entry, "lane", ""))
            if name and lane:
                ranking.setdefault(fold(name), lane)
        lanes = {fold(x): x for x in self.profile.lanes}

        def lane_of(platform: str, row_lanes: Sequence[str]) -> str:
            key = fold(platform)
            if key in ranking:
                return ranking[key]
            if key in lanes:
                return lanes[key]
            unique = {fold(x): x for x in row_lanes if x}
            return next(iter(unique.values())) if len(unique) == 1 else core.NOT_APPLICABLE

        return lane_of


# ---- family: job postings -----------------------------------------------------------------------
@dataclass
class _Posting:
    platforms: list[str]
    lanes: list[str]
    is_build: bool
    day: date | None
    url: str
    text: str


def _postings(b: _Builder, sheet: Sheet) -> list[_Posting]:
    """The account's postings in the window (undated ones are kept; dated-outside are not)."""
    out: list[_Posting] = []
    seen: set[str] = set()
    for row in sheet.rows:
        if fold(_val(row, "Entity")) not in b.account_labels:
            continue
        ident = _val(row, "Posting ID") or _val(row, "Source URL") or _val(row, "Title")
        if ident in seen:
            continue
        seen.add(ident)
        day = parse_day(_val(row, "Posted")) or parse_day(_val(row, "First seen"))
        if day is not None and not _inside(b.profile, day):
            continue
        out.append(
            _Posting(
                platforms=_split(_val(row, "Platforms named")),
                lanes=_split(_val(row, "Lane(s)")),
                is_build="build role" in _val(row, "Class").lower(),
                day=day,
                url=_val(row, "Source URL"),
                text=f"{_val(row, 'Title')} {_val(row, 'Platform sentences')}",
            )
        )
    return out


def _latest(postings: Sequence[_Posting]) -> date | None:
    days = [p.day for p in postings if p.day]
    return max(days) if days else None


def _jobs_family(b: _Builder) -> None:
    jobs = b.tables.get("jobs")
    sheet = _sheet(b.tables, "jobs", "Job postings")
    if jobs is None or sheet is None:
        b.skipped.append("jobs")
        return
    profile = b.profile
    lane_of = b.lane_lookup()
    account = profile.label
    by_platform: dict[str, list[_Posting]] = defaultdict(list)
    display: dict[str, str] = {}
    for posting in _postings(b, sheet):
        for name in posting.platforms:
            by_platform[fold(name)].append(posting)
            display.setdefault(fold(name), name)

    def newest_first(rows: Sequence[_Posting]) -> list[str]:
        ordered = sorted(rows, key=lambda p: p.day or date.min, reverse=True)
        return [p.url for p in ordered if p.url][:EVIDENCE_URLS_PER_SIGNAL]

    def grade_for(n: int) -> str:
        return "Confirmed" if n >= CONFIRMED_MIN_POSTINGS else "Probable"

    # platform hiring spike: latest 90 days of the window against the window's per-90-day average
    days_in_window = (profile.window_to - profile.window_from).days + 1
    periods = max(days_in_window / SPIKE_DAYS, 1.0)
    recent_from = profile.window_to - timedelta(days=SPIKE_DAYS - 1)
    for key, rows in sorted(by_platform.items()):
        dated = [p for p in rows if p.day]
        recent = [p for p in dated if p.day and p.day >= recent_from]
        average = len(dated) / periods
        if len(recent) < SPIKE_MIN_POSTINGS or len(recent) < SPIKE_FACTOR * average:
            continue
        name = display[key]
        sig = _Signal(
            text=(
                f"{account} posted {len(recent)} {name} roles in the latest {SPIKE_DAYS} days "
                f"({recent_from.isoformat()} to {profile.window_to.isoformat()}) against an "
                f"average of {average:.1f} per {SPIKE_DAYS} days over the window."
            ),
            kind="Platform hiring spike",
            lane=lane_of(name, [x for p in recent for x in p.lanes]),
            grade=grade_for(len(recent)),
            detected=profile.run_date,
            event=_latest(recent),
        )
        b.attach(sig, newest_first(recent))
        b.emit(sig)

    # build-role hiring: the per-platform Build-role counts the jobs table recorded, else the rows
    stats = jobs.stats.get("per_platform") if isinstance(jobs.stats, dict) else None
    stat_build: dict[str, int] = {}
    if isinstance(stats, Mapping):
        for name, counts in stats.items():
            if isinstance(counts, Mapping) and isinstance(counts.get("build"), int | float):
                stat_build[fold(str(name))] = int(counts["build"])
                display.setdefault(fold(str(name)), str(name))
    build_rows = {k: [p for p in v if p.is_build] for k, v in by_platform.items()}
    for key in sorted(set(stat_build) | {k for k, v in build_rows.items() if v}):
        rows = build_rows.get(key, [])
        count = stat_build.get(key, len(rows))
        if count <= 0:
            continue
        name = display[key]
        sig = _Signal(
            text=f"{account} has {_plural(count, 'in-window build-role posting')} naming {name}.",
            kind="Build-role hiring",
            lane=lane_of(name, [x for p in rows for x in p.lanes]),
            grade=grade_for(count),
            detected=profile.run_date,
            event=_latest(rows),
        )
        b.attach(sig, newest_first(rows))
        b.emit(sig)

    # program or migration wording on the platform sentences of the postings
    for key, rows in sorted(by_platform.items()):
        hits = [p for p in rows if _POSTING_PROGRAM.search(p.text)]
        if len(hits) < PROGRAM_MIN_POSTINGS:
            continue
        name = display[key]
        sig = _Signal(
            text=(
                f"{_plural(len(hits), 'in-window posting')} naming {name} describe migration, "
                f"rollout or transformation work at {account}."
            ),
            kind="Program or migration",
            lane=lane_of(name, [x for p in hits for x in p.lanes]),
            grade=grade_for(len(hits)),
            detected=profile.run_date,
            event=_latest(hits),
        )
        b.attach(sig, newest_first(hits))
        b.emit(sig)

    _entity_signals(b, jobs)


def _entity_signals(b: _Builder, jobs: RepairedTable) -> None:
    """Rows tagged with an entity that is a business separate from the account (an entity rule
    that does not count as the account) point to a divestiture or acquisition."""
    profile = b.profile
    for rule in profile.entities:
        if rule.counts_as_account or not rule.label or rule.label == core.NOT_ACCOUNT:
            continue
        rows: dict[str, str] = {}
        for sheet in jobs.sheets:
            if "Entity" not in sheet.columns:
                continue
            for row in sheet.rows:
                if fold(_val(row, "Entity")) == fold(rule.label):
                    ident = _val(row, "Posting ID") or _val(row, "Source URL")
                    rows.setdefault(ident or str(len(rows)), _val(row, "Source URL"))
        if not rows:
            continue
        sig = _Signal(
            text=(
                f"{_plural(len(rows), 'job posting')} belong to {rule.label}, a business recorded "
                f"separately from {profile.label} (divested or acquired) and left out of "
                f"{profile.label}'s counts."
            ),
            kind="M&A / divestiture",
            grade="Probable",
            detected=profile.run_date,
            event=parse_day(rule.before or ""),
        )
        b.attach(sig, [u for u in rows.values() if u][:EVIDENCE_URLS_PER_SIGNAL])
        b.emit(sig)


# ---- family: news and its facts -----------------------------------------------------------------
def _whole_sentence(text: str) -> bool:
    """Starts with a capital, has at least six words and is not a runaway paragraph."""
    clean = " ".join((text or "").split())
    words = clean.split()
    return bool(clean) and clean[0].isupper() and 6 <= len(words) <= 60 and len(clean) <= 400


def _news_family(b: _Builder) -> None:
    news = _sheet(b.tables, "news", "News")
    if news is None:
        b.skipped.append("news")
        return
    facts = _sheet(b.tables, "news", "News facts")
    article_rows: dict[str, Mapping[str, str]] = {}
    for row in news.rows:
        article_rows.setdefault(_val(row, "Article ID") or _val(row, "Source URL"), row)
    facts_by_article: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in facts.rows if facts else ():
        facts_by_article[_val(row, "Article ID")].append(row)

    def emit(row: Mapping[str, str], text: str, grade_text: str) -> None:
        kind = classify_text(text)
        if kind is None:
            return
        grade = normalise_grade(grade_text or _val(row, "Fact grade"))
        # an extracted fragment ("roll out across Europe ...") is no signal: below Probable the
        # text must read as a whole sentence
        if grade == "Lead" and not _whole_sentence(text):
            return
        sig = _Signal(
            text=_sentence(text),
            kind=kind,
            grade=grade,
            detected=b.profile.run_date,
            event=parse_day(_val(row, "Published")),
        )
        b.attach(sig, [_val(row, "Source URL")])
        b.emit(sig)

    for article_id, article in article_rows.items():
        fact_rows = facts_by_article.get(article_id, [])
        if not fact_rows:
            headline = _val(article, "Headline")
            if headline:
                emit(article, headline, _val(article, "Fact grade"))
            continue
        for fact in fact_rows:
            text = _val(fact, "Fact")
            if text:
                emit(article, text, _val(fact, "Fact grade"))


# ---- family: leadership -------------------------------------------------------------------------
def _inside(profile: WorkspaceProfile, day: date | None) -> bool:
    return day is not None and profile.window_from <= day <= profile.window_to


def _leadership_family(b: _Builder) -> None:
    sheet = _sheet(b.tables, "people", "Leadership")
    if sheet is None:
        b.skipped.append("people")
        return
    profile = b.profile
    for row in sheet.rows:
        name, role = _val(row, "Name"), _val(row, "Role")
        if not name or not role:
            continue
        status = _val(row, "Status")
        start, end = parse_day(_val(row, "Start date")), parse_day(_val(row, "End or retire date"))
        low = status.lower()
        if low.startswith("moved"):
            text = f"{name}, {role}, moved to a divested business."
            event = end or start
        elif low.startswith("departed") or _inside(profile, end):
            when = f" on {end.isoformat()}" if end else ""
            text = f"{name} is recorded as leaving the role of {role}{when}."
            event = end
        elif start is not None and _inside(profile, start):
            text = f"{name} started as {role} on {start.isoformat()}."
            event = start
        else:
            continue
        sig = _Signal(
            text=text,
            kind="Leadership change",
            grade="Lead" if low.startswith("unverified") or not status else "Confirmed",
            detected=profile.run_date,
            event=event,
        )
        b.attach(sig, [_val(row, "Source URL")])
        b.emit(sig)


# ---- stored signals -----------------------------------------------------------------------------
def _seed_family(b: _Builder, seeds: Sequence[SignalSeed]) -> None:
    for seed in seeds:
        title = " ".join((seed.title or "").split())
        if not title:
            continue
        kind = map_signal_type(seed.signal_type, title)
        if kind is None:
            b.dropped += 1
            b.exclusions.append(
                Exclusion("signals", title, "unknown signal type", seed.signal_type)
            )
            continue
        sig = _Signal(
            text=_sentence(title),
            kind=kind,
            lane=seed.lane.strip() or core.NOT_APPLICABLE,
            grade=normalise_grade(seed.grade),
            detected=seed.detected or b.profile.run_date,
            event=seed.event_date,
        )
        b.attach(sig, seed.source_urls, seed.source_codes)
        b.emit(sig)


# ---- the table ----------------------------------------------------------------------------------
def build_signals(
    profile: WorkspaceProfile,
    tables: Mapping[str, RepairedTable],
    seeds: Sequence[SignalSeed] = (),
) -> RepairedTable:
    """Build the Signals table from the other repaired tables and the stored signals. A missing
    table or sheet skips that family (listed in ``stats['skipped_families']``), never raises."""
    b = _Builder(profile, tables)
    _jobs_family(b)
    _news_family(b)
    _leadership_family(b)
    _seed_family(b, seeds)

    order = {t: i for i, t in enumerate(core.SIGNAL_TYPES)}
    ordered = sorted(
        b.found.values(),
        key=lambda s: (
            s.event is None,
            -(s.event.toordinal() if s.event else 0),
            order.get(s.kind, len(order)),
            s.text,
        ),
    )
    sheet = Sheet("Signals", core.SIGNAL_COLUMNS)
    for s in ordered:
        sheet.rows.append(
            {
                "Signal": s.text,
                "Type": s.kind,
                "Lane": s.lane or core.NOT_APPLICABLE,
                "Fact grade": s.grade,
                "Detected": core.iso(s.detected or profile.run_date),
                "Event date": core.iso(s.event),
                "Evidence (source codes)": s.evidence(),
            }
        )
    per_type = {t: sum(1 for s in ordered if s.kind == t) for t in core.SIGNAL_TYPES}
    stats: dict[str, Any] = {
        "total": len(ordered),
        "per_type": {t: n for t, n in per_type.items() if n},
        "dropped": b.dropped,
        "skipped_families": list(b.skipped),
    }
    notes = [f"Signal family skipped (table missing): {name}." for name in b.skipped]
    if not ordered:
        notes.append("No signals were generated from the available tables.")
    return RepairedTable(
        key=TABLE_KEY,
        title=TABLE_TITLE,
        sheets=[sheet],
        exclusions=b.exclusions,
        stats=stats,
        notes=notes,
    )
