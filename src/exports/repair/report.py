"""The repair report (``Logs/repair_report.md``) and the exclusions log (``Logs/exclusions.csv``).

Pure text builders: before/after per table, the acceptance results, the pipeline changes, what is
still Not done or Partial (and what the user must approve or enable), and an optional regression
block against baseline numbers. Nothing here names an account; every name comes from the
``WorkspaceProfile`` and the tables."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from exports.repair.core import (
    COVERAGE_COLUMNS,
    Exclusion,
    RepairedTable,
    WorkspaceProfile,
)

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
EXCLUSION_COLUMNS = ("Table", "Key", "Reason", "Detail")
_DUPLICATE_WORDS = ("duplicate", "merged")


@dataclass(frozen=True)
class TableBefore:
    """What a table looked like before the repair."""

    rows: int
    empty_cells: int = 0
    duplicate_keys: int = 0


def csv_document(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    """UTF-8 with a BOM, every field quoted, CRLF line ends, one header row; a field that a
    spreadsheet would run as a formula gets a leading apostrophe."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    def fix(value: object) -> str:
        text = "" if value is None else str(value)
        return "'" + text if text.startswith(_FORMULA_START) else text

    writer.writerow([fix(h) for h in header])
    for row in rows:
        writer.writerow([fix(v) for v in row])
    return out.getvalue().encode("utf-8-sig")


def _tables(tables: Mapping[str, RepairedTable] | Iterable[RepairedTable]) -> list[RepairedTable]:
    if isinstance(tables, Mapping):
        return list(tables.values())
    return list(tables)


def exclusions_csv(
    tables: Mapping[str, RepairedTable] | Iterable[RepairedTable],
    extra: Iterable[Exclusion] = (),
) -> bytes:
    """``Logs/exclusions.csv``: every Exclusion of every table (plus ``extra``, such as cells
    withheld while writing a workbook), sorted by table then key. Header only when none."""
    found: list[Exclusion] = [x for t in _tables(tables) for x in t.exclusions]
    found.extend(extra)
    found.sort(key=lambda x: (x.table, x.key, x.reason, x.detail))
    return csv_document(EXCLUSION_COLUMNS, [[x.table, x.key, x.reason, x.detail] for x in found])


# ---- Markdown helpers -----------------------------------------------------------------------


def _md(value: object) -> str:
    """One Markdown table cell: no pipes, no line breaks."""
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def _row(cells: Sequence[object]) -> str:
    return "| " + " | ".join(_md(c) for c in cells) + " |"


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = [_row(header), "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend(_row(r) for r in rows)
    return lines


def _count_empty(table: RepairedTable) -> int:
    return sum(1 for s in table.sheets for r in s.cell_rows() for c in r if not c.strip())


def _is_duplicate(x: Exclusion) -> bool:
    low = x.reason.casefold()
    return any(w in low for w in _DUPLICATE_WORDS)


def _flatten(prefix: str, value: Any, out: dict[str, Any], depth: int = 0) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float | str):
        out[prefix] = value
    elif isinstance(value, Mapping) and depth < 4:
        for k, v in value.items():
            if str(k).startswith("_"):
                continue
            _flatten(f"{prefix}.{k}", v, out, depth + 1)


def table_numbers(tables: Mapping[str, RepairedTable] | Iterable[RepairedTable]) -> dict[str, Any]:
    """Every scalar statistic of every table as ``<table key>.<stat>[.<sub>...]`` plus
    ``<table key>.rows`` (the main sheet's row count): the names a baseline refers to."""
    out: dict[str, Any] = {}
    for t in _tables(tables):
        out[f"{t.key}.rows"] = len(t.main.rows) if t.sheets else 0
        for k, v in t.stats.items():
            if str(k).startswith("_"):
                continue
            _flatten(f"{t.key}.{k}", v, out)
    return out


# ---- the unblock map: which reason words need which approval ---------------------------------

_UNBLOCK: tuple[tuple[tuple[str, ...], str], ...] = (
    (("consent",), "Approve people data consent for this workspace."),
    (
        ("mail", "drive", "not connected", "connect"),
        "Connect the mail and drive source (read only) for this workspace.",
    ),
    (
        ("cap", "budget", "limit", "ceiling", "execution", "token"),
        "Raise or lift the execution cap / approve the token and cost estimate.",
    ),
    (
        ("search", "searches", "query", "queries"),
        "Approve the specific web searches named in the reason (or enable the search provider).",
    ),
    (("disabled", "switched off", "not enabled", "off"), "Enable the feature named in the reason."),
    (("blocked", "paused"), "Remove the block named in the reason, then re-run this step."),
)


def unblock_for(reason: str) -> str:
    """What the user must approve or enable, derived from the words of the reason."""
    low = reason.casefold()
    for words, action in _UNBLOCK:
        if any(re.search(rf"\b{re.escape(w)}(?:s|ed|ing)?\b", low) for w in words):
            return action
    return "Review the reason; no approval keyword was found, so this may need a re-run."


def _coverage_rows(tables: Mapping[str, RepairedTable]) -> list[dict[str, str]]:
    table = next((t for t in tables.values() if t.key == "coverage"), None)
    if table is None:
        return []
    sheet = table.main
    return [{c: str(r.get(c, "")) for c in COVERAGE_COLUMNS} for r in sheet.rows]


# ---- the report -------------------------------------------------------------------------------


def build_report(
    profile: WorkspaceProfile,
    before: Mapping[str, TableBefore],
    tables: Mapping[str, RepairedTable],
    results: Sequence[Any],
    changes: Sequence[tuple[str, str, str, str]] = (),
    pending: Sequence[str] = (),
    *,
    baseline: Mapping[str, Any] | None = None,
    baseline_notes: Mapping[str, str] | None = None,
) -> str:
    """The Markdown of ``Logs/repair_report.md``. ``results`` are ``checks.CheckResult`` values
    (anything with ``name``, ``passed``, ``detail`` and ``numbers``). ``changes`` are
    ``(file, function, what, why)``; ``pending`` are extra Not done / Partial lines."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    lines: list[str] = [f"# Repair report: {profile.account_name}", ""]

    lines += ["## 1. Summary", ""]
    lines += [
        f"- Account: {profile.account_name}",
        f"- Run date: {profile.run_date.isoformat()}",
        f"- Window: {profile.window_from.isoformat()} to {profile.window_to.isoformat()}",
        f"- Tables repaired: {len(tables)}",
        f"- Acceptance checks: {passed} passed, {failed} failed ({len(results)} in all)",
        "- Result: " + ("every acceptance check passed" if not failed else "CHECKS FAILED"),
        "",
    ]

    lines += ["## 2. Before and after, per table", ""]
    rows: list[list[object]] = []
    detail_lines: list[str] = []
    for key in sorted(tables):
        t = tables[key]
        b = before.get(key) or before.get(t.key)
        rows_out = len(t.main.rows) if t.sheets else 0
        empty_after = _count_empty(t)
        dup_removed = sum(1 for x in t.exclusions if _is_duplicate(x))
        reasons = Counter(x.reason for x in t.exclusions if not _is_duplicate(x))
        filled = max(0, b.empty_cells - empty_after) if b else 0
        rows.append(
            [
                t.title + (" (internal)" if t.internal else ""),
                b.rows if b else "not recorded",
                rows_out,
                b.duplicate_keys if b else "not recorded",
                dup_removed,
                sum(reasons.values()),
                b.empty_cells if b else "not recorded",
                empty_after,
                filled,
            ]
        )
        if reasons:
            detail_lines.append(
                f"- {t.title}: " + "; ".join(f"{r}: {n}" for r, n in sorted(reasons.items()))
            )
    lines += _table(
        (
            "Table", "Rows in", "Rows out", "Duplicate keys before", "Duplicates removed",
            "Rows dropped", "Empty cells before", "Empty cells after", "Fields filled",
        ),
        rows,
    )  # fmt: skip
    lines += ["", "Rows dropped by reason:", ""]
    lines += detail_lines or ["- none"]
    lines.append("")

    lines += ["## 3. Acceptance checks", ""]
    check_rows = []
    for r in results:
        nums = ", ".join(f"{k}={v}" for k, v in sorted(dict(r.numbers).items()))
        check_rows.append([r.name, "PASS" if r.passed else "FAIL", r.detail, nums])
    lines += _table(("Check", "Result", "Detail", "Numbers"), check_rows)
    lines.append("")

    lines += ["## 4. Pipeline code changes", ""]
    if changes:
        lines += _table(("File", "Function", "What", "Why"), [list(c) for c in changes])
    else:
        lines.append("No pipeline code changed in this run.")
    lines.append("")

    lines += ["## 5. Still Not done or Partial", ""]
    not_done = [r for r in _coverage_rows(tables) if r["Status"].strip().casefold() != "done"]
    if not_done:
        lines += _table(
            ("#", "Research item", "Status", "Blocking reason", "What you must approve or enable"),
            [
                [r["#"], r["Research item"], r["Status"], r["Reason"], unblock_for(r["Reason"])]
                for r in not_done
            ],
        )
        lines.append("")
    notes = [(t.title, n) for t in (tables[k] for k in sorted(tables)) for n in t.notes]
    if notes:
        lines += ["Table notes:", ""]
        lines += [f"- {title}: {note} Action: {unblock_for(note)}" for title, note in notes]
        lines.append("")
    if pending:
        lines += ["Other pending items:", ""]
        lines += [f"- {p} Action: {unblock_for(p)}" for p in pending]
        lines.append("")
    if not (not_done or notes or pending):
        lines += ["Nothing is marked Not done or Partial.", ""]

    if baseline:
        lines += ["## 6. Regression against the baseline", ""]
        actual = table_numbers(tables)
        reg_rows = []
        for name in sorted(baseline):
            expected = baseline[name]
            if name not in actual:
                reg_rows.append(
                    [name, expected, "not compared", "not compared (no such number in this run)"]
                )
                continue
            got = actual[name]
            if got == expected:
                reg_rows.append([name, expected, got, "same"])
                continue
            note = (baseline_notes or {}).get(name) or (
                f"differs; the baseline was counted for a different date or scope, this run covers "
                f"{profile.window_from.isoformat()} to {profile.window_to.isoformat()}"
            )
            try:
                delta = f"{float(got) - float(expected):+g}"
            except (TypeError, ValueError):
                delta = "changed"
            reg_rows.append([name, expected, got, f"{delta}: {note}"])
        lines += _table(("Number", "Expected", "Actual", "Difference"), reg_rows)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
