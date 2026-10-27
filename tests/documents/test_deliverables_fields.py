"""The fields an uploaded People List or Job Postings row keeps (``documents.deliverables``): a
link field holds a web address, never its "Open profile" label or a script or mail target; no
field or quote keeps a work email or phone, wherever it was written; every value is bounded.
Also: the hyperlinks survive the parser sandbox. SYNTHETIC data only (no database)."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any

from openpyxl import Workbook

from documents.deliverables import DEFAULT_FIELD_CHARS, FIELD_CHARS, Found, SheetReader
from documents.sandbox import parse_in_sandbox
from research_files.config import load_config
from research_files.parsers.base import ParseContext
from research_files.parsers.tabular import XlsxParser

PEOPLE = ["Name", "Title as reported", "Meets seniority rule", "LinkedIn", "Second profile",
          "Location", "Note", "Work email"]  # fmt: skip
PROFILE = "https://www.linkedin.com/in/synthetic-person"


def passages(table: str, header: list[str], rows: list[list[Any]]) -> list[SimpleNamespace]:
    """Row passages as the chunker writes them: ``column: value`` pairs joined by ``; ``."""
    out = []
    for i, row in enumerate(rows, start=2):
        content = "; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False) if v)
        out.append(SimpleNamespace(content=content, location={"table": table, "row": i}, seq=i))
    return out


def read(table: str, header: list[str], rows: list[list[Any]]) -> list[Found]:
    reader = SheetReader()
    return [f for p in passages(table, header, rows) for f in reader.read(p)]


def test_people_fields_never_keep_a_work_email_or_phone() -> None:
    (found,) = read(
        "People",
        PEOPLE,
        [["Synthetic Person", "Director", "Yes", PROFILE, "", "Dublin (call +353 1 555 0100)",
          "Prefers synthetic.person@example.com or 555-010-0199", "synthetic@example.com"]],
    )  # fmt: skip
    assert found.fields["note"] == "Prefers [email removed] or [phone removed]"
    assert found.fields["location"] == "Dublin (call [phone removed])"
    assert found.fields["meets_seniority"] == "Yes"
    assert "@" not in found.quote and "555" not in found.quote
    assert "Work email" not in found.quote  # the column itself is never stored
    assert found.fields["link"] == PROFILE


def test_a_link_field_keeps_only_a_safe_web_address() -> None:
    rows = [
        ["Synthetic One", "Director", "", "Open profile", "", "", "", ""],
        ["Synthetic Two", "Director", "", "javascript:alert(1)", "mailto:x@example.com", "", "",
         ""],
        ["Synthetic Three", "Director", "", PROFILE + "-3", PROFILE + "-3b", "", "", ""],
    ]  # fmt: skip
    one, two, three = read("People", PEOPLE, rows)
    assert "link" not in one.fields  # a label without its address is no link
    assert "link" not in two.fields and "second_link" not in two.fields
    assert three.fields["link"] == PROFILE + "-3"  # a plain URL column still works
    assert three.fields["second_link"] == PROFILE + "-3b"
    jobs = ["Requisition", "Title", "Status", "Entity", "Link"]
    posting, label = read(
        "Jobs",
        jobs,
        [
            ["JR-1", "Synthetic Engineer", "Open", "Synthetic Ltd", "https://jobs.example/1"],
            ["JR-2", "Synthetic Analyst", "Open", "Synthetic Ltd", "Open posting"],
        ],
    )
    assert posting.fields["url"] == "https://jobs.example/1"
    assert posting.fields["entity"] == "Synthetic Ltd"
    assert "url" not in label.fields


def test_people_field_values_are_bounded() -> None:
    long = "x" * 5000
    (found,) = read(
        "People", PEOPLE, [["Synthetic Person", "Director", long, PROFILE, "", long, long, ""]]
    )
    assert len(found.fields["meets_seniority"]) == FIELD_CHARS["meets_seniority"]
    assert len(found.fields["location"]) == FIELD_CHARS["location"]
    assert len(found.fields["note"]) == DEFAULT_FIELD_CHARS
    assert len(found.quote) <= 600


def _linked_people_book() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "People"
    ws.append(["Name", "Title as reported", "LinkedIn", "Note"])
    ws.append(["Synthetic Person", "Director", "Open profile", "Open to work"])
    ws["C2"].hyperlink = PROFILE
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_an_uploaded_people_list_keeps_its_profile_link_end_to_end() -> None:
    ctx = ParseContext(load_config(), "people.xlsx")
    units = list(XlsxParser().units(io.BytesIO(_linked_people_book()), {}, ctx))
    unit = next(u for u in units if u.rows)
    assert unit.header is not None
    (found,) = read("People", unit.header, [cells for _, cells in unit.rows or []])
    assert found.fields["link"] == PROFILE


def test_the_parser_sandbox_reads_the_hyperlinks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # the link reader needs nothing the sandbox refuses (no network, process or write)
    path = tmp_path / "people.xlsx"
    path.write_bytes(_linked_people_book())

    async def units() -> list[Any]:
        return [
            u
            async for u in parse_in_sandbox(
                str(path),
                "xlsx",
                "people.xlsx",
                timeout_s=60,
                memory_mb=1536,
                cpu_s=60,
                max_text_bytes=10**6,
            )
        ]

    rows = [r for u in asyncio.run(units()) for r in (u.rows or [])]
    assert rows == [(2, ["Synthetic Person", "Director", PROFILE, "Open to work"])]


def test_a_people_list_with_a_location_column_is_read_as_people() -> None:
    # the People List's header: Title and Location also fit a job postings sheet
    header = [
        "No.",
        "Name",
        "Title as reported",
        "Level",
        "Meets seniority rule",
        "Lane",
        "Priority",
        "Found by",
        "LinkedIn",
        "Second profile",
        "Location",
        "Note",
    ]
    rows = [
        [1, "Synthetic Person", "Director, IT", "Director", "Yes", "Salesforce", "A", "search",
         PROFILE, "", "Dublin", ""],
        [2, "Synthetic Credentialed, PhD, PMP, CSM", "VP", "VP", "Yes", "Data", "B", "search",
         PROFILE + "-2", "", "Remote", ""],
        [3, "Python, SQL, Tableau, Excel", "", "", "", "", "", "", "", "", "", ""],
    ]  # fmt: skip
    found = read("People", header, rows)
    assert [f.kind for f in found] == ["people", "people"]
    assert found[0].fields["title"] == "Director, IT" and found[0].fields["location"] == "Dublin"
    assert found[1].value == "Synthetic Credentialed, PhD, PMP, CSM"  # credentials are no list


def test_the_platform_teams_sheet_of_the_current_workbook_is_read() -> None:
    header = [
        "Team",
        "Lane technologies named",
        "Postings count",
        "First activity",
        "Last activity",
        "Team technologies (name, postings)",
        "Team locations (place, postings)",
        "Job functions",
    ]
    (found,) = read(
        "Platform teams",
        header,
        [["Synthetic CRM Team", "Salesforce", "12", "2025-05-01", "2026-10-01", "Salesforce (9)",
          "Dublin (5)", "Engineering"]],
    )  # fmt: skip
    assert found.kind == "tech" and found.fields["category"] == "team"
    assert found.fields["sources"] == "12" and found.fields["function"] == "Engineering"
