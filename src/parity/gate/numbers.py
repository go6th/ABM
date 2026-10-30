"""Number consistency: every count in prose is a number the code computed.

What is compared. Every integer and whole-number percentage in (1) the prose fields of the
bundle and (2) the text of the rendered files (the Account Summary and Lane Highlights Word
files, and the text cells of the workbook Summary and Platform Ranking sheets) is looked up in
the computed set. The computed set is every integer under ``computed`` (``jobs_counts``,
``ent_counts``, ``lane_rows_account``, ``lane_rows_all``, ``people_total``, ``senior``,
``prio``, ``excluded``, ``pf``, ``tracker_tally``), ``ranking.base`` and each ranking row's
count and open, ``evidence.live_lane_count``, the meta pull counts (``live_capture_count``,
``feed_unique_links``, ``raw_board_counts``), the Summary values ``parity.counts`` derives
from the rows, the lengths of the bundle's lists (sources, postings read, rows, excluded
names, key people, extracts, live rows) and the plain sums and differences a writer states
(all entities together, closed = total minus open, rows before exclusion). A number that is
in none of these is a finding.

Which numbers are exempt, and why. Years (1900 to 2100) and date parts (a day number next to
a month name, a count of months, weeks or days: the research window), because they are dates,
not counts. Money (a currency sign before, million, billion, basis points after), because the
bundle computes no money. Label numbers (Tier 1, Part C, Pass 2, section 5, Q3, FY2025,
source 2), list markers and IDs (S41, J19, R-557322, versions such as 6.0), because they name
things. Decimals, because the computed set holds counts and shares only; a share printed with
a decimal is checked by the workbook check instead. Numbers inside verbatim material (the
evidence extracts' quotes, the live rows, detections, leads and the news event list, and the
whole Sources and Evidence file) are not scanned: they are the sources' numbers, not ours.

Severity. A number that stands next to a count noun of the research (postings, positions,
people, profiles, rows, senior, priority, lanes, requisitions, sources, names, hits, matches,
roles, records, open, closed, excluded, removed, captured) is a count claim and fails the
check: the reference's Summary says 553 divested-business postings were removed where
``ent_counts`` says 569, and that is exactly this case. A number stated as a bound or an
estimate (at least, more than, about, up to, top) is a warning, because it is not an equality.
Any other unmatched number (a figure quoted from a source: 200,000 users, 150 integrations) is
a warning for the reviewer. Whole-number percentages pass when they equal a computed share
rounded, or the rank rule's thresholds (15 and 5 percent); otherwise the same rules apply.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parity import counts
from parity.gate.files import (
    Bundle,
    docx_text,
    excel_files,
    is_people_workbook,
    word_files,
    xlsx_strings,
)
from parity.gate.report import CheckResult, Finding, result

NAME = "number_consistency"

_NUMBER = re.compile(r"(?<![\w./$€£-])(\d{1,3}(?:,\d{3})+|\d+)(?![\w/-]|[.,]\d)")
_PERCENT = re.compile(r"\s*(?:%|percent\b|per cent\b)")
_TOKEN = re.compile(r"[A-Za-z$€£%]+")
_CLAUSE = re.compile(r"[.;!?\n]")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_MONTHS = _words(
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec"
)
_TIME_UNITS = _words("month months year years week weeks day days hour hours am pm")
_MONEY_BEFORE = frozenset({"$", "€", "£", "usd", "eur", "gbp", "inr", "rs", "us$"})
_MONEY_AFTER = _words(
    "million billion thousand trillion bn mn m k crore lakh basis bps cents dollars euros pounds"
)
_LABELS = _words(
    "tier pass part section phase step level page table figure item sheet version no number "
    "rank source grade q fy h chapter row column line id code note footnote release wave stage"
)
_BOUNDS_BEFORE = _words(
    "least most than over under about around approximately roughly nearly almost top first "
    "last some up to within"
)
_BOUNDS_AFTER = frozenset({"or", "plus"})
COUNT_NOUNS = _words(
    "posting postings position positions people person persons profile profiles row rows "
    "senior priority lane lanes requisition requisitions source sources name names hit hits "
    "match matches role roles record records open closed excluded removed captured read entries"
)
RANK_THRESHOLDS = frozenset({15, 5})
_SKIPPED_EVIDENCE = frozenset({"extracts", "live_rows", "detections", "leads", "news_events"})


@dataclass(frozen=True)
class Mention:
    """One number found in text."""

    value: int
    percent: bool
    start: int
    exempt: str | None
    nouns: tuple[str, ...]
    bound: bool

    @property
    def is_count_claim(self) -> bool:
        """Whether the number stands next to a count noun and is not a bound."""
        return bool(self.nouns) and not self.bound


def _tokens(fragment: str) -> list[str]:
    return [t.casefold() for t in _TOKEN.findall(fragment)]


def mentions(text: str) -> list[Mention]:
    """Every integer and whole-number percentage in ``text`` with its context."""
    out: list[Mention] = []
    for m in _NUMBER.finditer(text):
        raw = m.group(1)
        value = int(raw.replace(",", ""))
        after_text = text[m.end() : m.end() + 48]
        before_text = text[max(0, m.start() - 48) : m.start()]
        percent = _PERCENT.match(after_text) is not None
        line_start = before_text.rstrip(" ") == "" or before_text.endswith("\n")
        list_marker = line_start and re.match(r"[.)]", after_text) is not None
        # the context stops at the clause: a noun in the previous sentence is not this number's
        before_text = _CLAUSE.split(before_text)[-1]
        after_text = _CLAUSE.split(after_text)[0]
        before = _tokens(before_text)[-3:]
        after = _tokens(after_text)[:5]
        prev = before[-1] if before else ""
        following = after[0] if after else ""
        if percent and after:
            after = after[1:]
            following = after[0] if after else ""
        exempt: str | None = None
        if not percent and 1900 <= value <= 2100 and "," not in raw and len(raw) == 4:
            exempt = "year"
        elif not percent and value <= 31 and (prev in _MONTHS or following in _MONTHS):
            exempt = "date"
        elif not percent and following in _TIME_UNITS:
            exempt = "duration"
        elif prev in _MONEY_BEFORE or following in _MONEY_AFTER:
            exempt = "money"
        elif not percent and prev in _LABELS:
            exempt = "label"
        elif not percent and list_marker:
            exempt = "list_marker"
        window = before[-2:] + after[:4]
        nouns = tuple(t for t in window if t in COUNT_NOUNS)
        bound = bool(set(before[-2:]) & _BOUNDS_BEFORE) or (
            len(after) > 1 and after[0] in _BOUNDS_AFTER and after[1] in ("more", "fewer", "less")
        )
        out.append(Mention(value, percent, m.start(), exempt, nouns, bound))
    return out


def _ints(value: Any, path: str, into: dict[int, set[str]]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        into.setdefault(value, set()).add(path)
    elif isinstance(value, float) and value.is_integer():
        into.setdefault(int(value), set()).add(path)
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _ints(v, f"{path}.{k}", into)
    elif isinstance(value, list | tuple):
        for v in value:
            _ints(v, path, into)


def computed_numbers(bundle: Bundle) -> dict[int, set[str]]:
    """Every computed integer with the keys it comes from (see the module docstring)."""
    into: dict[int, set[str]] = {}
    _ints(bundle.get("computed") or {}, "computed", into)
    ranking = bundle.get("ranking") or {}
    _ints(ranking.get("base"), "ranking.base", into)
    for row in ranking.get("rows") or ():
        _ints(row.get("count"), f"ranking.{row.get('platform')}.count", into)
        _ints(row.get("open"), f"ranking.{row.get('platform')}.open", into)
        _ints(int(row.get("count") or 0) - int(row.get("open") or 0), "ranking.closed", into)
    meta = bundle.get("meta") or {}
    for key in ("live_capture_count", "feed_unique_links", "raw_board_counts"):
        _ints(meta.get(key), f"meta.{key}", into)
    prose = bundle.get("prose") or {}
    evidence = prose.get("evidence") or {}
    _ints(evidence.get("live_lane_count"), "evidence.live_lane_count", into)
    for key, value in counts.summary_values(bundle).items():
        _ints(value, f"summary.{key}", into)
    lengths = {
        "sources": bundle.get("sources") or {},
        "jobs_read": bundle.get("jobs_read") or {},
        "jobs_rows": bundle.get("jobs_rows") or (),
        "people_rows": bundle.get("people_rows") or (),
        "overrides": bundle.get("overrides") or (),
        "stack_rows": bundle.get("stack_rows") or (),
        "team_rows": bundle.get("team_rows") or (),
        "ranking.rows": ranking.get("rows") or (),
        "ranking.seen_once": ranking.get("seen_once") or (),
        "key_people": prose.get("key_people") or (),
        "news": prose.get("news") or (),
        "hypotheses": prose.get("hypotheses") or (),
        "tracker_p1": prose.get("tracker_p1") or (),
        "tracker_p2": prose.get("tracker_p2") or (),
    }
    for key, items in (bundle.get("people_excluded") or {}).items():
        lengths[f"people_excluded.{key}"] = items or ()
    for key in ("extracts", "live_rows", "leads", "detections", "news_events"):
        lengths[f"evidence.{key}"] = evidence.get(key) or ()
    for tier, rows in (prose.get("signals") or {}).items():
        lengths[f"signals.{tier}"] = rows or ()
    for key, items in lengths.items():
        _ints(len(items), f"len.{key}", into)
    computed = bundle.get("computed") or {}
    ent = {k: int(v) for k, v in (computed.get("ent_counts") or {}).items()}
    excluded = {k: int(v) for k, v in (computed.get("excluded") or {}).items()}
    _ints(sum(ent.values()), "sum.ent_counts", into)
    # the tally line under the trackers states the rows of both passes together
    _ints(
        len(prose.get("tracker_p1") or ()) + len(prose.get("tracker_p2") or ()),
        "len.tracker_rows",
        into,
    )
    _ints(sum(excluded.values()), "sum.excluded", into)
    _ints(int(computed.get("people_total") or 0) + sum(excluded.values()), "people_before", into)
    _ints(
        int(computed.get("lane_rows_all") or 0) - int(computed.get("lane_rows_account") or 0),
        "lane_rows_other",
        into,
    )
    for lane, jc in (computed.get("jobs_counts") or {}).items():
        _ints(int(jc.get("total") or 0) - int(jc.get("open") or 0), f"jobs.{lane}.closed", into)
    return into


def computed_percentages(bundle: Bundle) -> set[int]:
    """Whole-number percentages a writer may state: each ranking share rounded, each priority
    share of the senior people, the senior share of all people, and the rank thresholds."""
    out = set(RANK_THRESHOLDS)
    for row in counts.platform_ranking(bundle):
        out.add(round(row.share * 100))
    computed = bundle.get("computed") or {}
    senior = int(computed.get("senior") or 0)
    total = int(computed.get("people_total") or 0)
    if senior:
        for n in (computed.get("prio") or {}).values():
            out.add(round(int(n) * 100 / senior))
    if total:
        out.add(round(senior * 100 / total))
    return out


def _prose_strings(value: Any, path: str = "prose") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if path == "prose.evidence" and k in _SKIPPED_EVIDENCE:
                continue
            yield from _prose_strings(v, f"{path}.{k}")
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _prose_strings(v, path)


def prose_text(bundle: Bundle) -> str:
    """The bundle's prose as one text, one field per line (verbatim material left out)."""
    return "\n".join(text for _, text in _prose_strings(bundle.get("prose") or {}))


def rendered_texts(files: list[Path]) -> dict[str, str]:
    """The text the check scans per rendered file: Word files other than Sources and Evidence,
    and the text cells of the Summary and Platform Ranking sheets."""
    out: dict[str, str] = {}
    for path in word_files(files):
        if path.name.endswith("_Sources_and_Evidence.docx"):
            continue
        out[path.name] = docx_text(path.read_bytes())
    for path in excel_files(files):
        cells = [
            text
            for sheet, _coord, text in xlsx_strings(path)
            if sheet in ("Summary", "Platform Ranking")
            and not (
                is_people_workbook(path) and sheet == "Summary" and "excluded" in text.casefold()
            )
        ]
        if cells:
            out[path.name] = "\n".join(cells)
    return out


def _nearest(value: int, known: Mapping[int, set[str]]) -> str:
    close = [k for k in known if k and abs(k - value) <= max(2, value // 10)]
    if not close:
        return ""
    best = min(close, key=lambda k: abs(k - value))
    return f"; nearest computed {best} ({sorted(known[best])[0]})"


Keyed = tuple[tuple[int, bool, str], Finding]


def _check(
    text: str, where: str, known: Mapping[int, set[str]], percentages: set[int]
) -> list[Keyed]:
    """Findings for one text keyed by ``(value, percent, severity)``, one per key."""
    out: list[Keyed] = []
    seen: set[tuple[int, bool, str]] = set()
    for m in mentions(text):
        if m.exempt is not None:
            continue
        if m.percent:
            if m.value in percentages:
                continue
        elif m.value in known:
            continue
        severity = "fail" if m.is_count_claim else "warn"
        key = (m.value, m.percent, severity)
        if key in seen:
            continue
        seen.add(key)
        shown = f"{m.value} percent" if m.percent else str(m.value)
        context = f" near {', '.join(m.nouns)}" if m.nouns else ""
        hint = "" if m.percent else _nearest(m.value, known)
        message = f"{shown}{context} is not a computed value{hint}"
        out.append((key, Finding(NAME, message, where, severity)))
    return out


def check_text(
    text: str,
    where: str,
    known: Mapping[int, set[str]],
    percentages: set[int],
) -> list[Finding]:
    """Findings for one text: one per distinct unmatched number, severity by the rules."""
    return [finding for _, finding in _check(text, where, known, percentages)]


def run_numbers(files: list[Path], bundle: Bundle) -> CheckResult:
    """The check over the bundle prose and the rendered files: one finding per distinct number
    and severity, naming every place it appears (a rendered file prints the prose, so the same
    number is found twice)."""
    known = computed_numbers(bundle)
    percentages = computed_percentages(bundle)
    texts = {"bundle prose": prose_text(bundle), **rendered_texts(files)}
    merged: dict[tuple[int, bool, str], tuple[Finding, list[str]]] = {}
    for where, text in texts.items():
        for key, f in _check(text, where, known, percentages):
            merged.setdefault(key, (f, []))[1].append(where)
    findings = [
        Finding(NAME, f.message, ", ".join(places), f.severity) for f, places in merged.values()
    ]
    return result(NAME, findings, {"computed_values": len(known), "scanned": list(texts)})
