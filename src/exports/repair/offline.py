"""Offline inputs of the repair stage: read an existing "Download everything" zip, or the
workbooks of a finished reference workspace, into ``records.py`` values. Used to measure a
defective export (the "before" of the report), to repair an export whose raw pulls are not
reachable, and to prove the dynamic pipeline on real data (``scripts/repair/regress.py``).

Nothing here is specific to an account: the loaders read columns by their headings, the same for
any workspace. A cell the old export left empty stays empty here; the builders decide what it
becomes."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from exports.repair.keys import canonical_url, norm_query
from exports.repair.records import (
    CoverageRecord,
    JobRecord,
    PersonRecord,
    SearchCall,
    SourceRecord,
    TechRecord,
)

_DAY = re.compile(r"^(\d{1,2}) ([A-Za-z]{3,9}) (\d{4})$")
_MONTH_NAMES = "jan feb mar apr may jun jul aug sep oct nov dec".split()  # noqa: SIM905
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}


@dataclass(frozen=True)
class LegacyTable:
    """One CSV of an old export: its header and rows (cells as text)."""

    name: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def dicts(self) -> Iterator[dict[str, str]]:
        """Rows keyed by heading (a short row is padded)."""
        for r in self.rows:
            padded = (*r, *([""] * (len(self.header) - len(r))))
            yield dict(zip(self.header, padded, strict=False))


def parse_day(value: str) -> date | None:
    """A date from ``2026-10-28``, ``28 Oct 2026`` or a timestamp; ``Today`` and ``2 days ago``
    are not dates (they were relative to the day the export was made)."""
    text = (value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    m = _DAY.match(text)
    if m and m.group(2)[:3].lower() in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def read_export_zip(path: Path) -> tuple[dict[str, LegacyTable], list[str]]:
    """The CSV tables of an old export (by file stem) and every member name."""
    tables: dict[str, LegacyTable] = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for name in names:
            if not name.lower().endswith(".csv") or "/" not in name:
                continue
            rows = list(csv.reader(io.StringIO(z.read(name).decode("utf-8-sig"))))
            if rows:
                stem = name.rsplit("/", 1)[1][:-4]
                tables[stem] = LegacyTable(stem, tuple(rows[0]), tuple(tuple(r) for r in rows[1:]))
    return tables, names


def readme_paths(readme: str) -> list[str]:
    """The folder names and paths an old README promises (`Deliverables/`, `Tables/`...)."""
    return sorted(
        set(re.findall(r"\b((?:Deliverables|Tables|Internal - your files|Logs)/?)", readme))
    )


def legacy_jobs(table: LegacyTable, collected: date | None = None) -> list[JobRecord]:
    """The old Job postings table as records (columns Role, Lane, Location, Posted, Status,
    Posting): no description, so no platform can be found in it; the old export's own lane cell
    is kept as the stored platform list."""
    out = []
    for i, r in enumerate(table.dicts()):
        lanes = tuple(x.strip() for x in r.get("Lane", "").split(",") if x.strip())
        out.append(
            JobRecord(
                key=f"legacy:{i}",
                url=r.get("Posting", ""),
                title=r.get("Role", ""),
                location=r.get("Location", ""),
                posted_at=parse_day(r.get("Posted", "")),
                status=r.get("Status", ""),
                stored_platforms=lanes,
                collected=collected,
                from_fact=r.get("Status", "") == "Found",
            )
        )
    return out


def legacy_people(table: LegacyTable) -> list[PersonRecord]:
    """The old People table as records. Its rows came from news text (no profile, no employer)
    unless they carry a LinkedIn link."""
    out = []
    for i, r in enumerate(table.dicts()):
        link = r.get("LinkedIn", "").strip()
        out.append(
            PersonRecord(
                key=f"legacy:{i}",
                name=r.get("Name", ""),
                title=r.get("Title as reported", ""),
                priority=r.get("Priority", ""),
                profile_url=link if link.startswith("http") else "",
                origin="import" if "linkedin.com/" in link else "news_fact",
            )
        )
    return out


def legacy_tech(table: LegacyTable) -> list[TechRecord]:
    """The old Tech stack table as records (website detections and facts)."""
    out = []
    for r in table.dicts():
        url = r.get("Source", "")
        out.append(
            TechRecord(
                technology=r.get("Technology", ""),
                detections=int(r["Sources"]) if r.get("Sources", "").isdigit() else 0,
                first_seen=r.get("First seen", ""),
                last_seen=r.get("Last seen", ""),
                source_url=url,
                cross_check=r.get("Cross check", ""),
            )
        )
    return out


def legacy_sources(table: LegacyTable) -> list[SourceRecord]:
    """The old Sources table as records."""
    out = []
    for r in table.dicts():
        code = r.get("Code", "")
        out.append(
            SourceRecord(
                code=code,
                url=r.get("Link", ""),
                title=r.get("Title", ""),
                publisher=r.get("Publisher", ""),
                published=parse_day(r.get("Published", "")),
                read_only=code == "Read",
            )
        )
    return out


def legacy_searches(table: LegacyTable) -> list[SearchCall]:
    """The old Web searches table as records (one per logged row)."""
    out = []
    for r in table.dicts():
        sites = tuple(x.strip() for x in r.get("Sites", "").split(",") if x.strip())
        day = parse_day(r.get("When", ""))
        pages = r.get("Pages returned", "")
        out.append(
            SearchCall(
                query=r.get("Searched for", ""),
                run_at=datetime(day.year, day.month, day.day) if day else None,
                pages=int(pages) if pages.isdigit() else 0,
                sites=sites,
            )
        )
    return out


def legacy_coverage(table: LegacyTable) -> list[CoverageRecord]:
    """The old Coverage table as records."""
    out = []
    for i, r in enumerate(table.dicts(), start=1):
        out.append(
            CoverageRecord(
                row_key=str(i),
                item=r.get("Research item", ""),
                number=int(r["#"]) if r.get("#", "").isdigit() else i,
                priority=r.get("Priority", ""),
                status=r.get("Status", ""),
                reason=r.get("Reason", ""),
                last_run=parse_day(r.get("Checked", "")),
            )
        )
    return out


def measure(table: LegacyTable, key_columns: Sequence[str]) -> dict[str, int]:
    """The "before" numbers of one old table: rows, empty cells, duplicate keys and, per column,
    how many cells are empty."""
    empties: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    for r in table.dicts():
        for col, value in r.items():
            if col != "#" and not str(value).strip():
                empties[col] += 1
        key = "|".join(
            canonical_url(r.get(c, ""))
            if "ink" in c or "ource" in c or c == "Posting"
            else norm_query(r.get(c, ""))
            for c in key_columns
        )
        if key.strip("|"):
            keys[key] += 1
    return {
        "rows": len(table.rows),
        "empty_cells": sum(empties.values()),
        "duplicate_keys": sum(n - 1 for n in keys.values() if n > 1),
        **{f"empty:{c}": n for c, n in empties.items()},
    }


# ---- the finished reference workspace (the owner's workbooks) --------------------------------
def _sheet_rows(path: Path, sheet: str, header_row_has: str) -> Iterator[dict[str, Any]]:
    """Rows of ``sheet`` below the first row whose first cell is ``header_row_has``, keyed by
    heading, with each link cell's address under ``<heading>#link``."""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    ws = wb[sheet]
    head: list[str] | None = None
    for row in ws.iter_rows():
        values = [c.value for c in row]
        if head is None:
            if values and str(values[0] or "").strip() == header_row_has:
                head = [str(v or "").strip() for v in values]
            continue
        if not any(v not in (None, "") for v in values):
            continue
        out: dict[str, Any] = {}
        for name, cell in zip(head, row, strict=False):
            out[name] = cell.value
            if cell.hyperlink and cell.hyperlink.target:
                out[f"{name}#link"] = cell.hyperlink.target
        yield out


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def reference_jobs(path: Path, collected: date | None = None) -> list[JobRecord]:
    """The postings of a finished reference Job Postings workbook (sheet ``Jobs``) as records:
    its description column carries the posting text the platforms are searched in."""
    out = []
    for i, r in enumerate(_sheet_rows(path, "Jobs", "Requisition")):
        out.append(
            JobRecord(
                key=f"ref:{i}",
                url=_s(r.get("Link#link")) or _s(r.get("Link")),
                title=_s(r.get("Title")),
                location=_s(r.get("Location")),
                first_seen=parse_day(_s(r.get("First seen"))),
                last_seen=parse_day(_s(r.get("Last seen"))),
                status=_s(r.get("Status")),
                description=_s(r.get("Full description")),
                requisition_id=_s(r.get("Requisition")),
                seniority=_s(r.get("Seniority")),
                stored_class=_s(r.get("Class")),
                collected=collected,
            )
        )
    return out


def reference_entity_labels(path: Path) -> Mapping[str, int]:
    """How many postings the reference tagged to each entity (the reference's own answer, used
    to compare the pipeline's tagging with it)."""
    return Counter(_s(r.get("Entity")) for r in _sheet_rows(path, "Jobs", "Requisition"))


def reference_people(path: Path) -> list[PersonRecord]:
    """The people of a finished reference People List (sheet ``People``) as records."""
    out = []
    for i, r in enumerate(_sheet_rows(path, "People", "No.")):
        out.append(
            PersonRecord(
                key=f"ref:{i}",
                name=_s(r.get("Name")),
                title=_s(r.get("Title as reported")),
                level=_s(r.get("Level")),
                lanes=tuple(x.strip() for x in _s(r.get("Lane")).split(",") if x.strip()),
                priority=_s(r.get("Priority")),
                profile_url=_s(r.get("LinkedIn#link")),
                location=_s(r.get("Location")),
                found_by=_s(r.get("Found by")),
                former_si=_s(r.get("Former SI and consulting employers")),
                note=_s(r.get("Note")),
                origin="profile",
            )
        )
    return out


def counts(items: Iterable[Any], key: Any) -> dict[str, int]:
    """``Counter`` of ``key(item)`` as a plain dict (for the regression block)."""
    return dict(Counter(key(i) for i in items))
