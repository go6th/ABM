"""The repair stage end to end: records in, repaired tables, acceptance checks and the report out.
Account-neutral; the same function serves a live workspace (records from ``readers.py``) and an
offline repair of an old export (records from ``offline.py``).

Order matters and is fixed: postings first (the ranking, teams and signals read them), then
technology, people, news, sources, searches, signals (read every table above) and finally
coverage (it reads the people and mail tables to say honestly what could not run)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from exports.partial import AREA_ROWS
from exports.repair import checks as CK
from exports.repair import report as RP
from exports.repair.changes import CHANGES
from exports.repair.core import UNKNOWN, Exclusion, RepairedTable, WorkspaceProfile
from exports.repair.coverage import build_coverage
from exports.repair.jobs import build_jobs
from exports.repair.mail import build_mail
from exports.repair.people import build_people
from exports.repair.records import DeliverableClaim, RepairInput
from exports.repair.searches import build_searches
from exports.repair.signals import build_signals
from exports.repair.sources import build_sources
from exports.repair.tech import build_tech

_AREAS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "people",
        ("people", "stakeholder", "buying group", "champion", "contact", "connection", "alumni"),
        ("people",),
    ),
    ("jobs", ("job", "hiring", "posting", "lane"), ("job", "posting")),
    ("tech", ("tech", "stack", "platform"), ("tech", "stack")),
)


@dataclass
class RepairResult:
    """What one repair produced."""

    tables: dict[str, RepairedTable]
    checks: list[CK.CheckResult]
    report_md: str
    before: dict[str, RP.TableBefore] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def exclusions(self) -> list[Exclusion]:
        """Every dropped or excluded row of every table."""
        return [x for t in self.tables.values() for x in t.exclusions]

    @property
    def passed(self) -> bool:
        """Whether every acceptance check passed."""
        return CK.all_passed(self.checks)


def repair_tables(profile: WorkspaceProfile, inp: RepairInput) -> dict[str, RepairedTable]:
    """Build every table (see the module docstring)."""
    tables: dict[str, RepairedTable] = {}
    jobs = build_jobs(profile, inp.jobs)
    tables["jobs"] = jobs
    tables["tech"] = build_tech(profile, inp.tech, jobs.stats.get("resolved", []))
    tables["people"] = build_people(profile, inp.people, inp.leadership)
    from exports.repair.news import build_news

    tables["news"] = build_news(profile, inp.articles)
    tables["sources"] = build_sources(profile, inp.sources, inp.articles)
    tables["searches"] = build_searches(profile, inp.searches)
    tables["mail"] = build_mail(profile, inp.mail)
    tables["signals"] = build_signals(profile, tables, inp.signal_seeds)
    claims = [*inp.claims, *claims_from_deliverables(inp)]
    tables["coverage"] = build_coverage(profile, inp.coverage, tables, claims)
    return tables


def claims_from_deliverables(inp: RepairInput) -> list[DeliverableClaim]:
    """What each current deliverable says about the areas the tracker covers. A deliverable that
    is present presents its area as done; it carries a visible ``Partial:`` note when its text
    has one. Areas and files are matched by generic words, never by an account."""
    claims: list[DeliverableClaim] = []
    texts = {name: _text_of(data) for name, data in inp.deliverables.items()}
    for area, item_words, file_words in _AREAS:
        files = [n for n in texts if any(w in n.lower() for w in file_words)]
        for record in inp.coverage:
            label = record.item.lower()
            if record.row_key not in AREA_ROWS.get(area, frozenset()) and not any(
                w in label for w in item_words
            ):
                continue
            for name in files:
                claims.append(
                    DeliverableClaim(
                        file=name,
                        section=area.title(),
                        row_key=record.row_key,
                        says_done=True,
                        carries_partial_note=bool(re.search(r"\bPartial:", texts[name])),
                    )
                )
    return claims


def _text_of(data: bytes) -> str:
    try:
        from documents.gate import client_text

        return client_text(data)
    except Exception:  # noqa: BLE001 - an unreadable file carries no note
        return ""


def pending_lines(tables: Mapping[str, RepairedTable]) -> list[str]:
    """Extra "what the user must approve or enable" lines the tables' own notes carry."""
    return [f"{t.title}: {n}" for t in tables.values() for n in t.notes]


def repair(
    profile: WorkspaceProfile,
    inp: RepairInput,
    *,
    before: Mapping[str, RP.TableBefore] | None = None,
    zip_bytes: bytes | None = None,
    baseline: Mapping[str, Any] | None = None,
    baseline_notes: Mapping[str, str] | None = None,
    changes: Sequence[tuple[str, str, str, str]] = CHANGES,
) -> RepairResult:
    """Build the tables, run every acceptance check and write the report."""
    tables = repair_tables(profile, inp)
    # a News row's Published is a date the page, its URL or the filing gave (news.py never uses
    # the collection day), so a same-day article is proven by construction
    news = tables.get("news")
    proven = (
        [r["Article ID"] for r in news.main.rows if r.get("Published") != UNKNOWN] if news else []
    )
    results = CK.run_checks(profile, tables, inp, zip_bytes, allowed=proven)
    report = RP.build_report(
        profile,
        dict(before or {}),
        tables,
        results,
        changes,
        pending_lines(tables),
        baseline=baseline,
        baseline_notes=baseline_notes,
    )
    return RepairResult(tables, results, report, dict(before or {}))
