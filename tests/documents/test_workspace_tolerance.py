"""A later package is "90 to 95 percent the same" (owner 2026-11-05): its files named in words,
its deliverables folder capitalised or missing, no RESTORE.md, columns renamed a little, a new
column or sheet. The import must read it as it reads the original. No database."""

from __future__ import annotations

import io

import pytest

from documents.deliverables import _find, match
from documents.exact_copy import column_lines
from documents.workspace import MemberRole, deliverable_role, may_import, member_roles
from documents.workspace_import import sniff_kind

R = MemberRole


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("UCB_People_List.xlsx", R.DELIVERABLE_PEOPLE),
        ("UCB People List.xlsx", R.DELIVERABLE_PEOPLE),
        ("people list export (2).xlsx", R.DELIVERABLE_PEOPLE),
        ("UCB - Job Postings v2.xlsx", R.DELIVERABLE_JOBS),
        ("jobs.xlsx", R.DELIVERABLE_JOBS),
        ("UCB Tech Stack & Teams.xlsx", R.DELIVERABLE_TECH),
        ("UCB Account Summary (final).docx", R.DELIVERABLE_SUMMARY),
        ("UCB Sources & Evidence.docx", R.DELIVERABLE_EVIDENCE),
        ("Sources_and_Evidence.docx", R.DELIVERABLE_EVIDENCE),
        # the extension decides as much as the words: a people .docx is not the People List
        ("People notes.docx", None),
        ("Summary.xlsx", None),
        ("notes.xlsx", None),
        ("jobs.csv", None),
        ("BD_Internal_Only.docx", None),
        ("peoplesoft export.xlsx", None),  # a word, not a part of one
    ],
)
def test_a_deliverable_is_known_by_the_words_of_its_name(name: str, role: MemberRole) -> None:
    assert deliverable_role(name) == role


RENAMED = [
    "UCB workspace (Nov)/RESTORE.md",
    "UCB workspace (Nov)/Deliverables/UCB People List.xlsx",
    "UCB workspace (Nov)/Deliverables/UCB - Job Postings v2.xlsx",
    "UCB workspace (Nov)/Deliverables/UCB Account Summary (final).docx",
    "UCB workspace (Nov)/Deliverables/UCB Sources & Evidence.docx",
    "UCB workspace (Nov)/Deliverables/UCB Tech Stack & Teams.xlsx",
    "UCB workspace (Nov)/Deliverables/UCB Executive Brief.pptx",
    "UCB workspace (Nov)/data/news_events.json",
    "UCB workspace (Nov)/notes/pull_log.md",
]


def test_renamed_files_in_a_capitalised_folder_keep_their_roles() -> None:
    roles = member_roles(RENAMED)
    got = {n.rsplit("/", 1)[-1]: r for n, r in roles.items()}
    assert got["UCB People List.xlsx"] is R.DELIVERABLE_PEOPLE
    assert got["UCB - Job Postings v2.xlsx"] is R.DELIVERABLE_JOBS
    assert got["UCB Account Summary (final).docx"] is R.DELIVERABLE_SUMMARY
    assert got["UCB Sources & Evidence.docx"] is R.DELIVERABLE_EVIDENCE
    assert got["UCB Tech Stack & Teams.xlsx"] is R.DELIVERABLE_TECH
    assert got["UCB Executive Brief.pptx"] is R.DELIVERABLE_OTHER
    assert got["news_events.json"] is R.RAW_PULL and got["pull_log.md"] is R.NOTE
    # without the marker the renamed files still make it a workspace
    assert member_roles(RENAMED[1:]) == {n: roles[n] for n in RENAMED[1:]}


def test_a_package_zipped_without_its_folders_is_a_workspace() -> None:
    flat = ["UCB_People_List.xlsx", "UCB_Job_Postings.xlsx", "UCB_Account_Summary.docx", "x.txt"]
    roles = member_roles(flat)
    assert roles["UCB_People_List.xlsx"] is R.DELIVERABLE_PEOPLE
    assert roles["UCB_Account_Summary.docx"] is R.DELIVERABLE_SUMMARY
    assert roles["x.txt"] is R.OTHER
    # one deliverable on its own is still a loose upload, as before
    assert member_roles(["UCB_People_List.xlsx", "x.txt"]) == {}
    # files of a build or data folder never make a root
    assert member_roles(["build/people_list.xlsx", "build/jobs.xlsx"]) == {}


def test_a_word_file_named_in_no_known_way_is_never_read_as_rows() -> None:
    roles = member_roles([*RENAMED, "UCB workspace (Nov)/Deliverables/Board memo.docx"])
    memo = "UCB workspace (Nov)/Deliverables/Board memo.docx"
    assert roles[memo] is R.DELIVERABLE_OTHER
    assert not may_import(roles, memo)
    other_book = "UCB workspace (Nov)/Deliverables/export.xlsx"
    assert may_import(member_roles([*RENAMED, other_book]), other_book)


ORIGINAL_PEOPLE = ["No.", "Name", "Title as reported", "Level", "Priority", "LinkedIn", "Note"]
CHANGED_PEOPLE = ["Full Name", "Job Title", "Seniority level", "Priority", "LinkedIn URL", "Team"]
ORIGINAL_JOBS = ["Requisition", "Title", "Platforms named", "Status", "First seen", "Link"]
CHANGED_JOBS = ["Req ID", "Title", "Platforms", "Status", "First seen", "Posting URL", "Salary"]


def test_a_renamed_column_is_read_as_the_original_one() -> None:
    people = match(CHANGED_PEOPLE)
    assert people is not None and people.kind == "people"
    assert _find(CHANGED_PEOPLE, people.fields["title"], words=True) == "Job Title"
    assert _find(CHANGED_PEOPLE, people.fields["level"], words=True) == "Seniority level"
    assert _find(CHANGED_PEOPLE, people.fields["link"], words=True) == "LinkedIn URL"
    jobs = match(CHANGED_JOBS)
    assert jobs is not None and jobs.kind == "jobs"
    assert _find(CHANGED_JOBS, jobs.fields["lanes"], words=True) == "Platforms"
    assert _find(CHANGED_JOBS, jobs.fields["url"], words=True) == "Posting URL"
    assert _find(CHANGED_JOBS, jobs.fields["requisition"], words=True) == "Req ID"
    # the original headers read exactly as before
    p0 = match(ORIGINAL_PEOPLE)
    assert p0 is not None and _find(ORIGINAL_PEOPLE, p0.fields["title"]) == "Title as reported"
    j0 = match(ORIGINAL_JOBS)
    assert j0 is not None and _find(ORIGINAL_JOBS, j0.fields["lanes"]) == "Platforms named"


def test_the_word_match_never_decides_a_sheets_kind_and_skips_sentences() -> None:
    # "Platform sentences" holds the word "platform" (the tech key) but the key needs the name
    assert match(["Title", "Status", "Platform sentences", "Location"]).kind == "jobs"  # type: ignore[union-attr]
    # a long header that holds the words is a sentence of its own, not the field
    assert _find(["The title the person had"], ("title",), words=True) is None


def _book(sheets: dict[str, list[list[object]]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first and wb.active is not None else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_a_workbook_named_in_no_known_way_is_known_by_its_sheets() -> None:
    people = _book(
        {
            "About": [["Exported 2026-11-05"]],
            "Contacts": [CHANGED_PEOPLE, ["Ada Byrne", "CIO", "2 VP", "A", "", "IT"]] * 1,
        }
    )
    assert sniff_kind("export.xlsx", people) == "people"
    jobs = _book({"Sheet1": [CHANGED_JOBS, ["1", "SAP Lead", "SAP", "Open", "2026-10-01"]]})
    assert sniff_kind("export.xlsx", jobs) == "jobs"
    assert sniff_kind("export.xlsx", _book({"Notes": [["a", "b"], ["c", "d"]]})) is None
    assert sniff_kind("export.docx", people) is None
    assert sniff_kind("export.xlsx", b"not a workbook") is None


def test_the_import_report_names_the_new_and_renamed_columns() -> None:
    book = _book(
        {
            "People list": [CHANGED_PEOPLE, ["Ada Byrne", "CIO", "2 VP", "A", "", "IT"]],
            "Vendor notes": [["Vendor", "Note"], ["Acme", "integrator"]],
        }
    )
    lines = column_lines({"UCB People List.xlsx": book})
    people = next(line for line in lines if "/ People list:" in line)
    assert "read as people, 1 rows" in people
    assert "Job Title (read as title)" in people and "Seniority level (read as level)" in people
    assert people.endswith("kept as written only: Team")
    vendor = next(line for line in lines if "/ Vendor notes:" in line)
    assert "kept as written, 1 rows" in vendor


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("UCB_People_List.xlsx", "UCB_People_List.xlsx"),
        ("UCB People List.xlsx", "UCB_People_List.xlsx"),
        ("UCB Sources & Evidence.docx", "UCB_Sources_and_Evidence.docx"),
        ("UCB Account Summary (final).docx", "UCB_Account_Summary_final.docx"),
        ("..x.xlsx", "x.xlsx"),
    ],
)
def test_an_uploaded_name_is_registered_in_a_safe_form(name: str, safe: str) -> None:
    import re

    from documents.workspace_import import safe_filename

    got = safe_filename(name)
    assert got == safe
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", got)  # the deliverables check


def test_one_column_is_never_read_as_two_things() -> None:
    from documents.deliverables import field_columns

    # the exact name claims its column first; the word match only takes what nobody claimed
    cols = ["Name", "Job Title", "Title as reported", "Seniority level", "Priority"]
    kind = match(cols)
    assert kind is not None
    got = field_columns(cols, kind)
    assert got["title"] == "Title as reported" and got["level"] == "Seniority level"
    assert len(set(got.values())) == len(got)  # no column read twice
    assert "Name" not in got.values()  # the key is never a field


def _people_book(title: str = "People") -> bytes:
    return _book({title: [CHANGED_PEOPLE, ["Ada Byrne", "CIO", "2 VP", "A", "", "IT"]]})


def test_the_file_of_each_kind_is_chosen_the_same_way_whatever_the_zip_order() -> None:
    from documents.workspace_import import choose_files

    exact = "ws/deliverables/UCB_People_List.xlsx"
    worded = "ws/deliverables/UCB People List.xlsx"
    old = "ws/deliverables/UCB_People_List old.xlsx"
    body = _people_book()
    files = {old: body + b"", worded: _people_book("P"), exact: _people_book("Q")}
    roles = member_roles(["ws/RESTORE.md", *files])
    for order in (files, dict(reversed(list(files.items())))):
        assert choose_files(order, roles)["people"][0] == exact
    # without the exact name, the current file beats the old copy
    two = {old: body, worded: _people_book("P")}
    assert choose_files(two, member_roles(["ws/RESTORE.md", *two]))["people"][0] == worded


def test_a_workbook_whose_sheets_disagree_with_its_name_is_registered_by_its_sheets() -> None:
    from documents.workspace_import import choose_files

    named_teams = "ws/deliverables/UCB Teams.xlsx"  # the name says tech, the sheets say people
    files = {named_teams: _people_book()}
    roles = member_roles(["ws/RESTORE.md", *files])
    assert roles[named_teams] is R.DELIVERABLE_TECH
    chosen = choose_files(files, roles)
    assert set(chosen) == {"people"}
    # a Word file has no sheets: its name decides; an empty file is never chosen
    docs = {"ws/deliverables/UCB Account Summary.docx": b"docx", "ws/deliverables/x jobs.xlsx": b""}
    assert set(choose_files(docs, member_roles(["ws/RESTORE.md", *docs]))) == {"summary"}
