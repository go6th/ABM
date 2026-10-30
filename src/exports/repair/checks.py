"""The acceptance checks of the repair stage (spec: "Acceptance checks"). ``run_checks`` runs every
check against the repaired tables and the repair input and returns one ``CheckResult`` per check;
nothing raises: a check that cannot run reports itself failed. Account-neutral: names, platforms
and entity labels come from the ``WorkspaceProfile`` and the tables."""

from __future__ import annotations

import importlib
import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from exports.repair.core import (
    OUTSIDE_WINDOW,
    RepairedTable,
    Sheet,
    WorkspaceProfile,
    is_placeholder,
)
from exports.repair.keys import canonical_url, fold, norm_name, norm_query
from exports.repair.package import README, validate_readme
from exports.repair.records import RepairInput
from exports.urlcheck import is_safe_http_url

_MAX_LISTED = 5
_CROSS_CHECK = frozenset({"both", "postings only", "detection only"})
_BARE_MARKERS = frozenset(
    {"not found", "not applicable", "unknown", "outside window", "not the account"}
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CODE = re.compile(r"^\s*S[-_ ]?0*(\d+)\s*$", re.IGNORECASE)
_SPLIT = re.compile(r"\s*[;,|]\s*")


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one acceptance check and the numbers it saw."""

    name: str
    passed: bool
    detail: str
    numbers: dict[str, Any] = field(default_factory=dict)


def all_passed(results: Sequence[CheckResult]) -> bool:
    """True when every check passed (and there is at least one)."""
    return bool(results) and all(r.passed for r in results)


# ---- helpers ----------------------------------------------------------------------------------


def _find(tables: Mapping[str, RepairedTable], key: str) -> RepairedTable | None:
    if key in tables:
        return tables[key]
    return next((t for t in tables.values() if t.key == key), None)


def _sheet(table: RepairedTable | None, name: str) -> Sheet | None:
    if table is None:
        return None
    return next((s for s in table.sheets if s.name.casefold() == name.casefold()), None)


def _cell(row: Mapping[str, Any], col: str) -> str:
    value = row.get(col, "")
    return "" if value is None else str(value).strip()


def _ok(name: str, detail: str, **numbers: Any) -> CheckResult:
    return CheckResult(name, True, detail, dict(numbers))


def _fail(name: str, detail: str, **numbers: Any) -> CheckResult:
    return CheckResult(name, False, detail, dict(numbers))


def _result(name: str, bad: Sequence[str], detail_ok: str, **numbers: Any) -> CheckResult:
    if not bad:
        return _ok(name, detail_ok, **numbers)
    shown = "; ".join(bad[:_MAX_LISTED])
    more = f" (and {len(bad) - _MAX_LISTED} more)" if len(bad) > _MAX_LISTED else ""
    return _fail(name, f"{len(bad)} problem(s): {shown}{more}", problems=len(bad), **numbers)


def _skipped(name: str, why: str) -> CheckResult:
    return _ok(name, f"skipped: {why}", skipped=1)


def _code_key(code: str) -> str:
    """``S1``, ``S-0001`` and ``s 01`` are one code."""
    m = _CODE.match(code)
    return f"S{int(m.group(1))}" if m else code.strip().casefold()


def _date_part(value: str) -> str:
    m = _DATE.search(value)
    return m.group(0) if m else ""


def _platform_names(profile: WorkspaceProfile) -> set[str]:
    """Folded names of the platform dictionary (lanes and ranking)."""
    platforms = profile.platforms
    if platforms is None:
        try:
            from parity.platforms import default_platforms

            platforms = default_platforms()
        except Exception:  # noqa: BLE001 - no dictionary: the check reports it
            return set()
    names = {fold(getattr(x, "name", "")) for x in getattr(platforms, "lanes", ())}
    names |= {fold(getattr(x, "name", "")) for x in getattr(platforms, "ranking", ())}
    return {n for n in names if n}


def _account_labels(profile: WorkspaceProfile) -> set[str]:
    labels = {profile.label, profile.account_short, profile.account_name, profile.account_label}
    labels |= {e.label for e in profile.entities if e.counts_as_account}
    return {fold(x) for x in labels if x}


def _split_platforms(value: str) -> list[str]:
    return [fold(p) for p in _SPLIT.split(value) if p.strip() and not is_placeholder(p)]


# ---- keys per table ---------------------------------------------------------------------------


def _key_rules(
    profile: WorkspaceProfile,
) -> dict[str, list[tuple[str, Callable[[Mapping[str, Any]], str]]]]:
    del profile

    def col(name: str) -> Callable[[Mapping[str, Any]], str]:
        return lambda r: fold(_cell(r, name))

    def url(name: str) -> Callable[[Mapping[str, Any]], str]:
        return lambda r: "" if is_placeholder(_cell(r, name)) else canonical_url(_cell(r, name))

    def person(r: Mapping[str, Any]) -> str:
        link = _cell(r, "LinkedIn")
        if link and not is_placeholder(link):
            return "in:" + canonical_url(link)
        name = norm_name(_cell(r, "Name"))
        return f"name:{name}|{fold(_cell(r, 'Entity'))}" if name else ""

    return {
        "jobs": [
            ("Job postings", col("Posting ID")),
            ("All postings (unfiltered)", col("Posting ID")),
        ],
        "people": [("People", person)],
        "tech": [("Platform Ranking", col("Platform")), ("Confirmed stack", col("Technology"))],
        "news": [("News", col("Article ID")), ("News", url("Source URL"))],
        "sources": [("Sources", url("Link"))],
        "searches": [("Searches", lambda r: norm_query(_cell(r, "Query")))],
        "signals": [
            ("Signals", lambda r: f"{fold(_cell(r, 'Signal'))}|{fold(_cell(r, 'Type'))}")
        ],
        "coverage": [("Coverage", col("#"))],
    }  # fmt: skip


def _key_sheet(table: RepairedTable, name: str, first: bool) -> Sheet | None:
    found = _sheet(table, name)
    if found is None and first and table.sheets:
        return table.sheets[0]
    return found


def check_duplicate_keys(
    profile: WorkspaceProfile, tables: Mapping[str, RepairedTable]
) -> CheckResult:
    """One row per entity: no key appears twice in a table."""
    name = "no_duplicate_keys"
    rules = _key_rules(profile)
    bad: list[str] = []
    numbers: dict[str, Any] = {}
    checked = 0
    for table in tables.values():
        for index, (sheet_name, keyer) in enumerate(rules.get(table.key, [])):
            sheet = _key_sheet(table, sheet_name, first=index == 0)
            if sheet is None:
                continue
            keys = [k for k in (keyer(r) for r in sheet.rows) if k and not is_placeholder(k)]
            checked += 1
            dupes = [k for k, n in Counter(keys).items() if n > 1]
            numbers[f"{table.key}.{sheet.name}"] = len(dupes)
            bad.extend(f"{table.title}/{sheet.name}: key {k!r} repeated" for k in dupes)
    if not checked:
        return _skipped(name, "no table with a key rule")
    return _result(name, bad, f"{checked} key set(s) unique", **numbers)


# ---- cells ------------------------------------------------------------------------------------


def _looks_like_marker(value: str) -> bool:
    low = value.strip().casefold()
    return (
        low in _BARE_MARKERS
        or low.startswith("not collected")
        or low.startswith("closed (not found")
    )


def check_no_empty_cells(tables: Mapping[str, RepairedTable]) -> CheckResult:
    """Every cell is filled or carries an allowed placeholder in its exact form."""
    name = "no_empty_cell_without_placeholder"
    bad: list[str] = []
    cells = 0
    for table in tables.values():
        for sheet in table.sheets:
            for r_no, row in enumerate(sheet.rows, start=2):
                for col in sheet.columns:
                    cells += 1
                    value = row.get(col)
                    text = "" if value is None else str(value)
                    where = f"{table.title}/{sheet.name} row {r_no} column {col!r}"
                    if not text.strip():
                        bad.append(f"{where}: empty")
                    elif _looks_like_marker(text) and not is_placeholder(text):
                        bad.append(f"{where}: malformed placeholder {text.strip()[:40]!r}")
    return _result(name, bad, f"{cells} cells, all filled or marked", cells=cells)


def check_published_vs_collected(
    tables: Mapping[str, RepairedTable], allowed: Collection[str] = ()
) -> CheckResult:
    """A published or posted date is never just the collection date, unless the source proves it
    (``allowed`` holds the row ids: Posting ID, Article ID or source Code)."""
    name = "published_not_collected"
    pairs = {
        "jobs": [("Job postings", "Posted", "Collected", "Posting ID")],
        "news": [("News", "Published", "Collected", "Article ID")],
        "sources": [("Sources", "Published", "Collected", "Code")],
    }
    allow = {str(a) for a in allowed}
    bad: list[str] = []
    rows_seen = 0
    for table in tables.values():
        for sheet_name, pub, col_collected, ident in pairs.get(table.key, []):
            sheet = _sheet(table, sheet_name)
            if sheet is None:
                continue
            for row in sheet.rows:
                rows_seen += 1
                published = _date_part(_cell(row, pub))
                if published and published == _date_part(_cell(row, col_collected)):
                    row_id = _cell(row, ident)
                    if row_id not in allow:
                        bad.append(f"{table.title}: {ident} {row_id} {pub} equals {col_collected}")
    return _result(name, bad, f"{rows_seen} rows compared", rows=rows_seen)


# ---- people -----------------------------------------------------------------------------------

_NAME_STOP = frozenset(
    {
        "the", "and", "of", "at", "for", "to", "in", "as", "is", "was", "has", "have", "will",
        "new",
        "chief", "officer", "president", "director", "manager", "vice", "head", "senior", "lead",
        "ceo", "cfo", "cto", "cio", "coo", "vp", "svp", "evp", "global", "group", "team", "joins",
        "joined", "appointed", "named", "announces", "announced",
    }
)  # fmt: skip
_PARTICLES = frozenset(
    {"van", "von", "de", "del", "della", "der", "den", "di", "da", "dos", "la", "le", "bin", "al",
     "el", "ibn", "st", "ter", "ten", "du", "y"}
)  # fmt: skip
_NAME_TOKEN = re.compile(r"^[^\W\d_][^\W\d_'.\-]*(?:['.\-][^\W\d_]+)*\.?$")


def _local_is_person_name(name: str) -> bool:
    """A minimal validator: two to five letter-only words, no titles or function words."""
    tokens = name.split()
    if not 2 <= len(tokens) <= 5:
        return False
    for t in tokens:
        low = t.casefold().rstrip(".")
        if low in _NAME_STOP or not _NAME_TOKEN.match(t):
            return False
        if low not in _PARTICLES and not t[0].isupper():
            return False
    return True


def _name_validator() -> Callable[[str], bool]:
    try:
        module = importlib.import_module("exports.repair.names")
        fn = getattr(module, "is_person_name", None)
        profile_fn = getattr(module, "is_profile_name", None)
        if callable(fn):
            # a People row's name came from a provider's name field: the same rule the builder
            # applies to it (a trailing initial or a short surname is a name there)
            return lambda n: bool(fn(n)) or bool(callable(profile_fn) and profile_fn(n))
    except Exception:  # noqa: BLE001 - the builder module is optional at this point
        pass
    return _local_is_person_name


def check_people(tables: Mapping[str, RepairedTable]) -> list[CheckResult]:
    """Valid names, none from news, LinkedIn a profile link or ``Not found``."""
    table = _find(tables, "people")
    names = ("people_names_valid", "people_none_from_news", "people_linkedin_filled")
    sheet = _sheet(table, "People") if table else None
    if table is None or sheet is None:
        return [_skipped(n, "no People table") for n in names]
    valid = _name_validator()
    bad_names: list[str] = []
    bad_news: list[str] = []
    bad_links: list[str] = []
    real = 0
    for r_no, row in enumerate(sheet.rows, start=2):
        who = _cell(row, "Name")
        if is_placeholder(who):  # the consent line
            continue
        real += 1
        if not valid(who):
            bad_names.append(f"row {r_no}: {who[:40]!r} is not a person name")
        if "news" in fold(_cell(row, "Found by")):
            bad_news.append(f"row {r_no}: found by {_cell(row, 'Found by')[:30]!r}")
        link = _cell(row, "LinkedIn")
        if not link or not (is_placeholder(link) or is_safe_http_url(link)):
            bad_links.append(f"row {r_no}: LinkedIn {link[:40]!r}")
    stat_news = sum(
        int(v)
        for k, v in table.stats.items()
        # ``dropped_news`` counts rows the builder REFUSED for coming from news: good news
        if isinstance(v, int | float)
        and "news" in str(k).casefold()
        and not str(k).casefold().startswith(("dropped", "refused", "excluded"))
    )
    if stat_news:
        bad_news.append(f"statistics report {stat_news} row(s) from news")
    return [
        _result(names[0], bad_names, f"{real} names valid", people=real),
        _result(names[1], bad_news, "no People row comes from news", from_news=stat_news),
        _result(names[2], bad_links, f"{real} rows have a LinkedIn link or Not found", people=real),
    ]


# ---- jobs -------------------------------------------------------------------------------------


def _jobs_sheets(table: RepairedTable) -> list[Sheet]:
    return [
        s
        for s in table.sheets
        if s.name.casefold() in ("job postings", "all postings (unfiltered)")
    ] or table.sheets[:1]


def _account_rows(profile: WorkspaceProfile, sheet: Sheet) -> list[Mapping[str, Any]]:
    labels = _account_labels(profile)
    return [r for r in sheet.rows if fold(_cell(r, "Entity")) in labels]


def _outside(row: Mapping[str, Any]) -> bool:
    return any(
        OUTSIDE_WINDOW.casefold() in _cell(row, c).casefold()
        for c in ("Status", "Posted", "First seen", "Last seen")
    )


def recompute_per_platform(profile: WorkspaceProfile, sheet: Sheet) -> dict[str, dict[str, int]]:
    """Postings and open postings per platform from the account rows of a Job postings sheet."""
    out: dict[str, dict[str, int]] = {}
    for row in _account_rows(profile, sheet):
        if _outside(row):
            continue
        for p in _split_platforms(_cell(row, "Platforms named")):
            slot = out.setdefault(p, {"postings": 0, "open": 0})
            slot["postings"] += 1
            slot["open"] += _cell(row, "Status").casefold() == "open"
    return out


def check_jobs(profile: WorkspaceProfile, tables: Mapping[str, RepairedTable]) -> list[CheckResult]:
    """Title is a job title, Status is Open or Closed*, Entity on every row, counts from account
    rows only and equal to the table's statistics."""
    names = ("jobs_title_not_platform", "jobs_status", "jobs_entity", "jobs_counts_account_only")
    table = _find(tables, "jobs")
    if table is None:
        return [_skipped(n, "no Jobs table") for n in names]
    platform_names = _platform_names(profile)
    bad_title: list[str] = []
    bad_status: list[str] = []
    bad_entity: list[str] = []
    rows = 0
    for sheet in _jobs_sheets(table):
        for r_no, row in enumerate(sheet.rows, start=2):
            rows += 1
            where = f"{sheet.name} row {r_no}"
            if fold(_cell(row, "Title")) in platform_names:
                bad_title.append(f"{where}: title {_cell(row, 'Title')!r} is a platform name")
            status = _cell(row, "Status")
            if status != "Open" and not status.startswith("Closed"):
                bad_status.append(f"{where}: status {status[:30]!r}")
            if not _cell(row, "Entity"):
                bad_entity.append(f"{where}: no Entity")
    results = [
        _result(names[0], bad_title, f"{rows} titles checked", rows=rows)
        if platform_names
        else _fail(names[0], "no platform dictionary to check titles against"),
        _result(names[1], bad_status, f"{rows} statuses valid", rows=rows),
        _result(names[2], bad_entity, f"{rows} rows have an Entity", rows=rows),
    ]
    results.append(_check_job_counts(profile, table, names[3]))
    return results


def _check_job_counts(profile: WorkspaceProfile, table: RepairedTable, name: str) -> CheckResult:
    sheet = _sheet(table, "Job postings") or table.main
    sheet_counts = recompute_per_platform(profile, sheet)
    stats = table.stats.get("per_platform")
    problems: list[str] = []
    numbers: dict[str, Any] = {"platforms": len(sheet_counts)}
    if isinstance(stats, Mapping):
        stated = {fold(str(k)): v for k, v in stats.items() if isinstance(v, Mapping)}
        for plat in sorted(set(stated) | set(sheet_counts)):
            s = stated.get(plat, {})
            c = sheet_counts.get(plat, {"postings": 0, "open": 0})
            for field_name in ("postings", "open"):
                if int(s.get(field_name, 0) or 0) != c[field_name]:
                    problems.append(
                        f"{plat} {field_name}: statistics {s.get(field_name, 0)}, "
                        f"account rows {c[field_name]}"
                    )
        numbers["account_postings_by_platform"] = sum(v["postings"] for v in sheet_counts.values())
    else:
        numbers["note"] = "no per_platform statistics; sheet counts only"
    resolved = table.stats.get("resolved")
    if isinstance(resolved, Sequence) and not isinstance(resolved, str):
        counted: Counter[str] = Counter()
        for p in resolved:
            if getattr(p, "counts_as_account", False) and not getattr(p, "outside_window", False):
                for plat in getattr(p, "platforms", ()):
                    counted[fold(str(plat))] += 1
        if isinstance(stats, Mapping):
            for plat, n in counted.items():
                have: Mapping[str, Any] = stats.get(plat) or next(
                    (v for k, v in stats.items() if fold(str(k)) == plat), {}
                )
                if int((have or {}).get("postings", 0) or 0) != n:
                    problems.append(f"{plat}: resolved postings {n} differ from statistics")
        numbers["resolved_account_postings"] = sum(counted.values())
    return _result(
        name, problems, "counts come from account rows and match the statistics", **numbers
    )


def account_postings(profile: WorkspaceProfile, table: RepairedTable) -> int:
    """Unique in-window account postings of the Job postings sheet."""
    sheet = _sheet(table, "Job postings") or table.main
    return sum(1 for r in _account_rows(profile, sheet) if not _outside(r))


# ---- tech -------------------------------------------------------------------------------------


def check_tech(tables: Mapping[str, RepairedTable]) -> list[CheckResult]:
    """Every platform found in postings is in the ranking; Cross check on every stack row."""
    names = ("tech_ranking_has_lane_platforms", "tech_cross_check_filled")
    table = _find(tables, "tech")
    if table is None:
        return [_skipped(n, "no Tech table") for n in names]
    ranking = _sheet(table, "Platform Ranking")
    jobs = _find(tables, "jobs")
    out: list[CheckResult] = []
    if ranking is None:
        out.append(_fail(names[0], "no Platform Ranking sheet"))
    elif jobs is None or not isinstance(jobs.stats.get("per_platform"), Mapping):
        out.append(_skipped(names[0], "no per_platform statistics from the Jobs table"))
    else:
        listed = {fold(_cell(r, "Platform")) for r in ranking.rows}
        found = [
            str(k)
            for k, v in jobs.stats["per_platform"].items()
            if isinstance(v, Mapping) and int(v.get("postings", 0) or 0) > 0
        ]
        missing = [
            f"{p} has postings but is not in Platform Ranking"
            for p in found
            if fold(p) not in listed
        ]
        out.append(
            _result(
                names[0],
                missing,
                f"{len(found)} platforms with postings all ranked",
                platforms=len(found),
            )
        )
    stack = _sheet(table, "Confirmed stack")
    if stack is None:
        out.append(_fail(names[1], "no Confirmed stack sheet"))
    else:
        bad = [
            f"row {i}: Cross check {_cell(r, 'Cross check')!r}"
            for i, r in enumerate(stack.rows, start=2)
            if _cell(r, "Cross check").casefold() not in _CROSS_CHECK
        ]
        out.append(
            _result(names[1], bad, f"{len(stack.rows)} rows cross checked", rows=len(stack.rows))
        )
    return out


# ---- sources ----------------------------------------------------------------------------------


def check_sources(tables: Mapping[str, RepairedTable], inp: RepairInput) -> list[CheckResult]:
    """Title, Publisher, Published filled; every cited code resolves; Confirmed facts cite."""
    names = ("sources_fields_filled", "citations_resolve", "confirmed_facts_cite_source")
    table = _find(tables, "sources")
    sheet = _sheet(table, "Sources") if table else None
    if sheet is None:
        return [
            _skipped(names[0], "no Sources table"),
            *[_skipped(n, "no Sources table") for n in names[1:]],
        ]
    bad = [
        f"{_cell(r, 'Code') or f'row {i}'}: {c} empty"
        for i, r in enumerate(sheet.rows, start=2)
        for c in ("Title", "Publisher", "Published")
        if not _cell(r, c)
    ]
    first = _result(names[0], bad, f"{len(sheet.rows)} sources filled", rows=len(sheet.rows))
    codes = {_code_key(_cell(r, "Code")) for r in sheet.rows if _cell(r, "Code")}
    cited = sorted({c for f in inp.cited_facts for c in f.codes})
    unresolved = [
        f"{c} does not resolve to a Sources row" for c in cited if _code_key(c) not in codes
    ]
    second = _result(
        names[1],
        unresolved,
        f"{len(cited)} cited codes resolve",
        cited=len(cited),
        sources=len(codes),
    )
    confirmed = [f for f in inp.cited_facts if f.grade.strip().casefold() == "confirmed"]
    uncited = [f"Confirmed fact {f.fact_id} cites no source" for f in confirmed if not f.codes]
    third = _result(
        names[2],
        uncited,
        f"{len(confirmed)} Confirmed facts all cite a source",
        confirmed=len(confirmed),
    )
    return [first, second, third]


# ---- coverage ---------------------------------------------------------------------------------


def check_coverage(tables: Mapping[str, RepairedTable], inp: RepairInput) -> CheckResult:
    """Deliverable claims agree with the Coverage statuses."""
    name = "coverage_agrees_with_deliverables"
    table = _find(tables, "coverage")
    if table is None:
        return _skipped(name, "no Coverage table")
    mismatches = table.stats.get("mismatches")
    if mismatches is None:
        sheet = _sheet(table, "Coverage") or table.main
        status = {_cell(r, "#"): _cell(r, "Status").casefold() for r in sheet.rows}
        number = {c.row_key: str(c.number) for c in inp.coverage}
        mismatches = [
            f"{c.file}/{c.section}: says done, Coverage item {c.row_key} is not Done"
            for c in inp.claims
            if c.says_done
            and not c.carries_partial_note
            and status.get(number.get(c.row_key, c.row_key), "done") != "done"
        ]
    if isinstance(mismatches, list | tuple | set | frozenset):
        items = [str(m) for m in mismatches]
    else:
        items = [f"{mismatches} mismatch(es) reported"] if mismatches else []
    return _result(name, items, "every claim agrees with Coverage", claims=len(inp.claims))


# ---- zip --------------------------------------------------------------------------------------


def check_zip(tables: Mapping[str, RepairedTable], zip_bytes: bytes | None) -> CheckResult:
    """The zip holds every path its README lists (and the Job postings workbook)."""
    name = "zip_contains_readme_paths"
    if zip_bytes is None:
        return _skipped(name, "no archive supplied")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            members = z.namelist()
            readme = z.read(README).decode("utf-8") if README in members else ""
    except (zipfile.BadZipFile, KeyError) as exc:
        return _fail(name, f"archive unreadable: {type(exc).__name__}")
    bad = validate_readme(readme, members) if readme else ["README.txt is missing"]
    jobs = _find(tables, "jobs")
    if jobs is not None:
        wanted = [
            m
            for m in members
            if m.endswith(".xlsx") and m.rsplit("/", 1)[-1].startswith(jobs.title)
        ]
        if not wanted:
            bad.append(f"{jobs.title}.xlsx is not in the archive")
    return _result(name, bad, f"{len(members)} members, all listed", members=len(members))


# ---- counts -----------------------------------------------------------------------------------


def check_counts(
    profile: WorkspaceProfile, tables: Mapping[str, RepairedTable], inp: RepairInput
) -> CheckResult:
    """Counts the deliverables quote equal the tables' counts (keys ``<table>.<statistic>``)."""
    name = "table_counts_match_deliverables"
    bad: list[str] = []
    notes: list[str] = []
    compared = 0
    for key, quoted in sorted(inp.quoted_counts.items()):
        table_key, _, stat = key.partition(".")
        table = _find(tables, table_key)
        actual: Any = table.stats.get(stat) if table is not None else None
        if actual is None and table is not None and key == "jobs.account_postings":
            actual = account_postings(profile, table)
        if actual is None and table is not None and stat == "total" and table_key == "people":
            actual = len(table.main.rows)
        if not isinstance(actual, int | float):
            notes.append(f"{key} not compared")
            continue
        compared += 1
        if int(actual) != int(quoted):
            bad.append(f"{key}: deliverables say {quoted}, table has {int(actual)}")
    detail = f"{compared} quoted count(s) match" + (
        f"; {len(notes)} skipped ({', '.join(notes)})" if notes else ""
    )
    return _result(name, bad, detail, compared=compared, skipped=len(notes))


# ---- the entry point --------------------------------------------------------------------------


def run_checks(
    profile: WorkspaceProfile,
    tables: Mapping[str, RepairedTable],
    inp: RepairInput,
    zip_bytes: bytes | None = None,
    *,
    allowed: Collection[str] = (),
) -> list[CheckResult]:
    """Every acceptance check, in a fixed order. A check that crashes is reported failed."""
    plan: list[tuple[str, Callable[[], CheckResult | list[CheckResult]]]] = [
        ("no_duplicate_keys", lambda: check_duplicate_keys(profile, tables)),
        ("no_empty_cell_without_placeholder", lambda: check_no_empty_cells(tables)),
        ("published_not_collected", lambda: check_published_vs_collected(tables, allowed)),
        ("people", lambda: check_people(tables)),
        ("jobs", lambda: check_jobs(profile, tables)),
        ("tech", lambda: check_tech(tables)),
        ("sources", lambda: check_sources(tables, inp)),
        ("coverage_agrees_with_deliverables", lambda: check_coverage(tables, inp)),
        ("zip_contains_readme_paths", lambda: check_zip(tables, zip_bytes)),
        ("table_counts_match_deliverables", lambda: check_counts(profile, tables, inp)),
    ]
    results: list[CheckResult] = []
    for label, run in plan:
        try:
            got = run()
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failed check
            results.append(_fail(label, f"check crashed: {type(exc).__name__}: {exc}"))
            continue
        results.extend(got if isinstance(got, list) else [got])
    return results
