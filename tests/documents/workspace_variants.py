"""Variants of a workspace zip: the same package changed the way a later one may differ from it
(owner 2026-11-05: "90 to 95 percent the same, a little up and down"), and what the upload made
of each. Used by ``test_workspace_variants_real_zip.py`` (the owner's own zip, by hand) and
``test_workspace_variants_db.py`` (the synthetic owner-shaped zip, in the suite).

A variant is ``bytes -> (bytes, expected kinds)``: the changed zip and the deliverable kind each
of its Word and Excel files must be registered as (by the file's name inside the zip).
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

KINDS_BY_SUFFIX = (
    ("_account_summary.docx", "summary"),
    ("_sources_and_evidence.docx", "evidence"),
    ("_people_list.xlsx", "people"),
    ("_job_postings.xlsx", "jobs"),
    ("_tech_stack_and_teams.xlsx", "tech"),
)
# the names a renamed package gives its files (the account name is kept in front)
RENAMED = {
    "summary": "{a} Account Summary (final).docx",
    "evidence": "{a} Sources & Evidence.docx",
    "people": "{a} People List.xlsx",
    "jobs": "{a} - Job Postings v2.xlsx",
    "tech": "{a} Tech Stack & Teams.xlsx",
}
# column renames in the People and Jobs sheets, and the new columns each gains
PEOPLE_RENAMES = {
    "Name": "Full Name",
    "Title as reported": "Job Title",
    "LinkedIn": "LinkedIn URL",
    "Level": "Seniority level",
}
JOBS_RENAMES = {"Requisition": "Req ID", "Link": "Posting URL", "Platforms named": "Platforms"}
PEOPLE_NEW = ("Email status", "Team")
JOBS_NEW = ("Salary range",)
NEW_SHEET = ("Vendor notes", ("Vendor", "Note"), (("Acme Systems", "integrator"),) * 3)

Variant = Callable[[bytes], tuple[bytes, dict[str, str]]]


def _kind(name: str) -> str | None:
    lower = name.casefold()
    return next((k for suffix, k in KINDS_BY_SUFFIX if lower.endswith(suffix)), None)


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def _zip(members: Mapping[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return out.getvalue()


def _account(members: Mapping[str, bytes]) -> str:
    for name in members:
        base = posixpath.basename(name)
        for suffix, _k in KINDS_BY_SUFFIX:
            if base.casefold().endswith(suffix):
                return base[: -len(suffix)]
    return "Account"


def _expected(members: Mapping[str, bytes]) -> dict[str, str]:
    return {posixpath.basename(n): k for n in members if (k := _kind(posixpath.basename(n)))}


def original(data: bytes) -> tuple[bytes, dict[str, str]]:
    return data, _expected(_members(data))


def renamed_files(data: bytes) -> tuple[bytes, dict[str, str]]:
    """Another top folder, ``Deliverables`` capitalised, the five files named in words."""
    members = _members(data)
    account = _account(members)
    out: dict[str, bytes] = {}
    expected: dict[str, str] = {}
    for name, body in members.items():
        parts = name.split("/")
        if len(parts) > 1:
            parts[0] = f"{account} workspace (Nov)"
        kind = _kind(parts[-1])
        if kind:
            parts[-2] = "Deliverables"
            parts[-1] = RENAMED[kind].format(a=account)
            expected[parts[-1]] = kind
        out["/".join(parts)] = body
    return _zip(out), expected


def no_marker(data: bytes) -> tuple[bytes, dict[str, str]]:
    """No RESTORE.md or MANIFEST.json, and the files renamed: neither marker nor suffix."""
    renamed, expected = renamed_files(data)
    members = {
        n: b
        for n, b in _members(renamed).items()
        if posixpath.basename(n) not in ("RESTORE.md", "MANIFEST.json")
    }
    return _zip(members), expected


def flat(data: bytes) -> tuple[bytes, dict[str, str]]:
    """Only the five files, at the zip's root."""
    members = _members(data)
    out = {posixpath.basename(n): b for n, b in members.items() if _kind(posixpath.basename(n))}
    return _zip(out), _expected(out)


def _edit_book(body: bytes, edit: Callable[[Any], None]) -> bytes:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(body))
    edit(book)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _header_row(ws: Any, wanted: str) -> int | None:
    for row in ws.iter_rows(min_row=1, max_row=40):
        if any(str(c.value or "").strip() == wanted for c in row):
            return int(row[0].row)
    return None


def _rename_and_add(ws: Any, renames: Mapping[str, str], new: tuple[str, ...]) -> None:
    head = _header_row(ws, next(iter(renames)))
    if head is None:
        return
    last = 0
    for cell in ws[head]:
        if cell.value is not None:
            last = max(last, int(cell.column))
            cell.value = renames.get(str(cell.value).strip(), cell.value)
    for i, title in enumerate(new, start=1):
        ws.cell(row=head, column=last + i, value=title)
        for r in range(head + 1, ws.max_row + 1):
            if any(c.value not in (None, "") for c in ws[r][:last]):
                ws.cell(row=r, column=last + i, value=f"{title} {r - head}")


def changed_columns(data: bytes) -> tuple[bytes, dict[str, str]]:
    """People and Jobs sheets renamed, columns renamed, new columns, a new sheet in the tech
    workbook."""
    members = _members(data)
    out: dict[str, bytes] = {}
    for name, body in members.items():
        kind = _kind(posixpath.basename(name))
        if kind == "people":

            def people(book: Any) -> None:
                ws = book["People"] if "People" in book.sheetnames else book.worksheets[-1]
                _rename_and_add(ws, PEOPLE_RENAMES, PEOPLE_NEW)
                ws.title = "People list"

            body = _edit_book(body, people)
        elif kind == "jobs":

            def jobs(book: Any) -> None:
                ws = book["Jobs"] if "Jobs" in book.sheetnames else book.worksheets[1]
                _rename_and_add(ws, JOBS_RENAMES, JOBS_NEW)
                ws.title = "Job postings"

            body = _edit_book(body, jobs)
        elif kind == "tech":

            def tech(book: Any) -> None:
                title, header, rows = NEW_SHEET
                ws = book.create_sheet(title)
                ws.append(list(header))
                for row in rows:
                    ws.append(list(row))

            body = _edit_book(body, tech)
        out[name] = body
    return _zip(out), _expected(out)


def mixed(data: bytes) -> tuple[bytes, dict[str, str]]:
    """Changed columns, renamed files and no marker together."""
    changed, _ = changed_columns(data)
    return no_marker(changed)


VARIANTS: dict[str, Variant] = {
    "original": original,
    "renamed_files": renamed_files,
    "no_marker": no_marker,
    "flat": flat,
    "changed_columns": changed_columns,
    "mixed": mixed,
}


# --- what the upload made of it ------------------------------------------------------------------


@dataclass
class Seen:
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


_SKIP_FOLDERS = frozenset({"build", "data", "notes"})


def deliverable_files(data: bytes) -> dict[str, bytes]:
    """Every Word and Excel file of the zip that is not a build, data or note file."""
    out: dict[str, bytes] = {}
    for name, body in _members(data).items():
        parts = [p.casefold() for p in name.split("/")]
        if _SKIP_FOLDERS & set(parts[:-1]):
            continue
        if re.search(r"\.(xlsx|docx)$", parts[-1]):
            out[name] = body
    return out


def _data_rows(grid: Any) -> int:
    return len(grid.rows) if len(grid.header) > 1 else 0


async def measure(
    tenant_id: UUID,
    account_id: UUID,
    document_id: UUID,
    data: bytes,
    expected: Mapping[str, str],
    *,
    payloads: Any | None = None,
) -> Seen:
    """What the upload stored against what the zip holds: every table cell for cell, every file
    registered by its kind with its bytes, and the people and posting rows."""
    import hashlib

    from account_intelligence.db.session import tenant_txn
    from documents import exact_copy as EC

    seen = Seen()
    files = deliverable_files(data)
    grids = [g for member, body in files.items() for g in EC.file_grids(member, body)]
    async with tenant_txn(tenant_id) as s:
        tables = await EC._stored_tables(s, document_id)
        matched = EC.match_tables(grids, tables)
        stored_ok = 0
        for i, grid in enumerate(grids):
            label = f"{posixpath.basename(grid.member)} / {grid.name}"
            t = matched.get(i)
            if t is None:
                seen.problems.append(f"{label}: not stored")
                continue
            rows = await EC._stored_rows(s, UUID(str(t.id)))
            diffs = EC.compare_grid(grid, list(t.columns or []), rows)
            if diffs:
                seen.problems.append(f"{label}: {diffs[0]}")
            else:
                stored_ok += 1
        seen.lines.append(f"- tables stored as written: {stored_ok} of {len(grids)}")

        run_id = await s.scalar(
            text("SELECT id FROM account_runs WHERE account_id = :a AND idempotency_key = :k"),
            {"a": account_id, "k": f"file-import-{document_id}"},
        )
        registered = (
            (
                await s.execute(
                    text("SELECT kind, sha256, filename FROM deliverables WHERE run_id = :r"),
                    {"r": run_id},
                )
            ).all()
            if run_id
            else []
        )
        by_sha = {str(r.sha256): r for r in registered}
        for member, body in files.items():
            base = posixpath.basename(member)
            want = expected.get(base)
            row = by_sha.get(hashlib.sha256(body).hexdigest())
            if want and (row is None or str(row.kind) != want):
                got = f"registered as {row.kind}" if row else "not registered"
                seen.problems.append(f"{base}: should be the {want} file, {got}")
        seen.lines.append(
            f"- files registered: {sorted(str(r.kind) for r in registered)} "
            f"(expected {sorted(expected.values())})"
        )

        people_want = sum(
            _data_rows(g) for g in grids if expected.get(posixpath.basename(g.member)) == "people"
        )
        jobs_want = sum(
            _data_rows(g) for g in grids if expected.get(posixpath.basename(g.member)) == "jobs"
        )
        people_have = (
            await s.scalar(
                text(
                    "SELECT count(*) FROM people WHERE run_id = :r AND merged_into IS NULL"
                    " AND erased_at IS NULL AND source_record_id NOT LIKE 'leadership:%'"
                ),
                {"r": run_id},
            )
            if run_id
            else 0
        )
        jobs_have = (
            await s.scalar(
                text("SELECT count(*) FROM job_postings WHERE run_id = :r"), {"r": run_id}
            )
            if run_id
            else 0
        )
        seen.lines.append(f"- people rows {people_have} / sheet rows {people_want}")
        seen.lines.append(f"- posting rows {jobs_have} / sheet rows {jobs_want}")
        if int(people_have or 0) != people_want:
            seen.problems.append(f"people: {people_have} stored of {people_want} sheet rows")
        if int(jobs_have or 0) != jobs_want:
            seen.problems.append(f"postings: {jobs_have} stored of {jobs_want} sheet rows")
    return seen


def render(variant: str, seen: Seen) -> str:
    verdict = "SAME" if not seen.problems else f"{len(seen.problems)} problem(s)"
    lines = [f"## {variant}: {verdict}", "", *seen.lines]
    lines += [f"- PROBLEM: {p}" for p in seen.problems[:30]]
    return "\n".join(lines) + "\n"
