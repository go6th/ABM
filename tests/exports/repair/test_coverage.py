"""Coverage table: status normalisation, evidence rule, blockers from the profile, claims check."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from exports.repair import core
from exports.repair.core import RepairedTable, Sheet, WorkspaceProfile
from exports.repair.coverage import (
    CLAIM_COLUMNS,
    build_coverage,
    claim_mismatches,
    evidence_cell,
    normalise_status,
)
from exports.repair.records import CoverageRecord, DeliverableClaim

ACCOUNTS = {
    "a": ("Quillfern Holdings", "Quillfern", "quillfern.example"),
    "b": ("Marlow Tideworks", "Marlow", "marlow.test"),
}


def make_profile(key: str = "a", *, people: bool = True, mail: bool = True) -> WorkspaceProfile:
    name, short, domain = ACCOUNTS[key]
    return WorkspaceProfile(
        account_name=name,
        account_short=short,
        domain=domain,
        run_date=date(2026, 10, 31),
        window_from=date(2025, 11, 1),
        window_to=date(2026, 10, 31),
        people_consent=people,
        mail_drive_connected=mail,
    )


def rec(key: str, item: str = "A research item", **kw: Any) -> CoverageRecord:
    return CoverageRecord(row_key=key, item=item, **kw)


def rows_by_item(table: RepairedTable) -> dict[str, dict[str, str]]:
    return {r["Research item"]: r for r in table.main.rows}


@pytest.fixture(params=["a", "b"])
def profile(request: pytest.FixtureRequest) -> WorkspaceProfile:
    return make_profile(request.param)


# ---- status and evidence -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("done", "Done"), ("DONE", "Done"), ("Completed", "Done"), ("partial", "Partial"),
        ("not_done", "Not done"), ("Not-Done", "Not done"), ("pending", "Not done"),
        ("blocked", "Blocked"), ("none found", "None found"), ("no_results", "None found"),
        ("", "Not checked"), ("unknown", "Not checked"), ("not checked", "Not checked"),
        ("wibble", None),
    ],
)  # fmt: skip
def test_normalise_status(word: str, expected: str | None) -> None:
    assert normalise_status(word) == expected


def test_done_with_evidence_keeps_status(profile: WorkspaceProfile) -> None:
    table = build_coverage(
        profile,
        [rec("k1", "Jobs crawl", status="Done", priority="High", evidence_rows=12,
             source_codes=("S4", "S1", "S4"), last_run=date(2026, 10, 30), reason="old note")],
        {},
    )  # fmt: skip
    [row] = table.main.rows
    assert row["Status"] == "Done"
    assert row["Reason"] == core.NOT_APPLICABLE
    assert row["Evidence (row counts / source codes)"] == "12 rows; S1, S4"
    assert row["Last run"] == "2026-10-30"
    assert row["Priority"] == "High"


def test_done_without_evidence_is_downgraded(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [rec("k1", "Jobs crawl", status="done")], {})
    [row] = table.main.rows
    assert row["Status"] == "Partial"
    assert row["Reason"] == "no evidence rows"
    assert row["Evidence (row counts / source codes)"] == core.NOT_FOUND
    assert row["Last run"] == core.UNKNOWN
    assert table.stats["downgraded"] == 1


def test_downgrade_keeps_the_recorded_reason_first(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [rec("k1", status="Done", reason="Only page one read")], {})
    assert table.main.rows[0]["Reason"] == "Only page one read; no evidence rows"


def test_done_with_only_codes_is_evidence(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [rec("k1", status="Done", source_codes=("S2",))], {})
    [row] = table.main.rows
    assert row["Status"] == "Done"
    assert row["Evidence (row counts / source codes)"] == "0 rows; S2"


def test_none_found_without_evidence_is_legitimate(profile: WorkspaceProfile) -> None:
    table = build_coverage(
        profile, [rec("k1", status="None found", reason="Searched, nothing")], {}
    )
    [row] = table.main.rows
    assert row["Status"] == "None found" and row["Reason"] == "Searched, nothing"


def test_no_cell_is_empty_and_reasons_never_blank(profile: WorkspaceProfile) -> None:
    records = [rec(f"k{i}", status=s) for i, s in enumerate(
        ["Done", "Partial", "Not done", "Blocked", "None found", "Not checked", "gibberish", ""]
    )]  # fmt: skip
    table = build_coverage(profile, records, {})
    for row in table.main.rows:
        assert set(row) == set(core.COVERAGE_COLUMNS)
        assert all(str(v).strip() for v in row.values())
        if row["Status"] != "Done":
            assert (
                row["Reason"] == core.not_collected("reason not recorded")
                or "evidence" in row["Reason"]
                or "not recognised" in row["Reason"]
            )
    unknown = next(r for r in table.main.rows if "not recognised" in r["Reason"])
    assert unknown["Status"] == "Not checked" and "gibberish" in unknown["Reason"]


def test_missing_priority_and_item_use_not_found(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [CoverageRecord(row_key="k", item="", status="Partial")], {})
    row = table.main.rows[0]
    assert row["Research item"] == core.NOT_FOUND and row["Priority"] == core.NOT_FOUND


def test_numbering_uses_record_number_else_sequence_and_sorts(profile: WorkspaceProfile) -> None:
    records = [rec("c", "Third", number=3, status="Partial"), rec("x", "Seq", status="Partial"),
               rec("a", "First", number=1, status="Partial")]  # fmt: skip
    table = build_coverage(profile, records, {})
    assert [(r["#"], r["Research item"]) for r in table.main.rows] == [
        ("1", "First"), ("2", "Seq"), ("3", "Third")
    ]  # fmt: skip


def test_duplicate_row_keys_keep_the_latest_run(profile: WorkspaceProfile) -> None:
    records = [
        rec("k", "Old", status="Partial", last_run=date(2026, 9, 1)),
        rec("k", "New", status="Done", evidence_rows=3, last_run=date(2026, 10, 1)),
    ]
    table = build_coverage(profile, records, {})
    assert [r["Research item"] for r in table.main.rows] == ["New"]
    assert table.exclusions[0].reason == "duplicate tracker row"


# ---- blockers ----------------------------------------------------------------------------------
PEOPLE_LABELS = [
    "Map the buying group", "Find platform stakeholders", "Identify champions and decision makers",
    "People search on LinkedIn", "Org chart of the data team",
]  # fmt: skip


@pytest.mark.parametrize("label", PEOPLE_LABELS)
def test_no_people_consent_blocks_people_items(label: str) -> None:
    profile = make_profile(people=False)
    table = build_coverage(
        profile, [rec("p", label, number=7, status="Done", evidence_rows=40)], {}
    )
    [row] = table.main.rows
    assert row["Status"] == "Not done" and row["Reason"] == "People data consent needed"


def test_people_blocker_leaves_other_items_and_blocked_status_alone() -> None:
    profile = make_profile(people=False)
    records = [
        rec("j", "Job postings crawl", number=5, status="Done", evidence_rows=9),
        rec("p", "Buying group", number=9, status="Blocked", reason="Provider quota"),
        rec("q", "Stakeholder map", number=10, status="Not done", reason="Not started"),
    ]
    got = rows_by_item(build_coverage(profile, records, {}))
    assert got["Job postings crawl"]["Status"] == "Done"
    assert got["Buying group"]["Status"] == "Blocked"
    assert got["Buying group"]["Reason"] == "Provider quota; People data consent needed"
    assert got["Stakeholder map"]["Status"] == "Not done"
    assert got["Stakeholder map"]["Reason"] == "People data consent needed"


def test_people_items_free_with_consent(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [rec("p", "Buying group", status="Done", evidence_rows=4)], {})
    assert table.main.rows[0]["Status"] == "Done"


def test_consent_missing_people_table_blocks_even_with_consent_flag() -> None:
    placeholder = RepairedTable("people", "People", [Sheet("People", core.PEOPLE_COLUMNS)],
                                stats={"consent_missing": True})  # fmt: skip
    table = build_coverage(
        make_profile(people=True),
        [rec("p", "Find stakeholders", status="Done", evidence_rows=4)],
        {"people": placeholder},
    )
    assert table.main.rows[0]["Status"] == "Not done"
    assert any("People data consent needed" in n for n in table.notes)


def test_mail_not_connected_blocks_items_one_and_two_only() -> None:
    profile = make_profile(mail=False)
    records = [
        rec(f"k{i}", f"Item {i}", number=i, status="Done", evidence_rows=3) for i in (1, 2, 3)
    ]
    got = rows_by_item(build_coverage(profile, records, {}))
    for label in ("Item 1", "Item 2"):
        assert got[label]["Status"] == "Not done"
        assert got[label]["Reason"] == "Mail and drive not connected"
    assert got["Item 3"]["Status"] == "Done"


def test_mail_table_reporting_not_connected_also_blocks() -> None:
    mail = RepairedTable(
        "mail", "Mail", [Sheet("Mail", core.MAIL_COLUMNS)], stats={"connected": False}
    )
    table = build_coverage(
        make_profile(mail=True),
        [rec("k", "Mail search", number=1, status="Done", evidence_rows=2)],
        {"mail": mail},
    )  # fmt: skip
    assert table.main.rows[0]["Status"] == "Not done"


def test_both_blockers_join_reasons() -> None:
    profile = make_profile(people=False, mail=False)
    table = build_coverage(
        profile,
        [rec("k", "Mail about the buying group", number=1, status="Done", evidence_rows=2)],
        {},
    )
    assert (
        table.main.rows[0]["Reason"] == "Mail and drive not connected; People data consent needed"
    )


# ---- claims ------------------------------------------------------------------------------------
def claim(
    key: str, says_done: bool = True, partial: bool = False, section: str = "S"
) -> DeliverableClaim:
    return DeliverableClaim("Summary.docx", section, key, says_done, partial)


def test_claims_ok_and_mismatch(profile: WorkspaceProfile) -> None:
    records = [
        rec("done", "Done item", status="Done", evidence_rows=2),
        rec("part", "Partial item", status="Partial", reason="half"),
        rec("nodone", "Done w/o evidence", status="Done"),
    ]
    claims = [
        claim("done", section="One"),
        claim("part", section="Two"),
        claim("part", says_done=False, section="Three"),
        claim("part", partial=True, section="Four"),
        claim("nodone", section="Five"),
    ]
    table = build_coverage(profile, records, {}, claims)
    sheet = table.sheets[1]
    assert sheet.name == "Deliverable claims" and sheet.columns == CLAIM_COLUMNS
    results = {r["Section"]: r["Result"] for r in sheet.rows}
    assert results["One"] == "OK" and results["Three"] == "OK" and results["Four"] == "OK"
    assert results["Two"] == "Mismatch: section says done but coverage is Partial"
    assert results["Five"] == "Mismatch: section says done but coverage is Partial"
    assert table.stats["mismatch_count"] == 2 and len(table.stats["mismatches"]) == 2
    assert claim_mismatches(table) == [
        "Summary.docx > Two: Mismatch: section says done but coverage is Partial",
        "Summary.docx > Five: Mismatch: section says done but coverage is Partial",
    ]
    assert all(str(v).strip() for r in sheet.rows for v in r.values())
    first = sheet.rows[0]
    assert first["Says done"] == "Yes" and first["Partial note present"] == "No"
    assert first["Coverage item"] == "Done item" and first["Coverage status"] == "Done"


def test_claim_on_missing_item_is_a_mismatch_when_it_says_done(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [], {}, [claim("ghost")])
    assert claim_mismatches(table)
    assert table.sheets[1].rows[0]["Coverage item"] == "ghost"
    assert table.stats["claims_without_item"] == ["ghost"]


def test_claims_see_the_blockers() -> None:
    profile = make_profile(people=False)
    records = [rec("p", "Buying group", number=4, status="Done", evidence_rows=40)]
    table = build_coverage(profile, records, {}, [claim("p")])
    assert claim_mismatches(table) == [
        "Summary.docx > S: Mismatch: section says done but coverage is Not done"
    ]
    ok = build_coverage(profile, records, {}, [claim("p", partial=True)])
    assert claim_mismatches(ok) == []


def test_no_claims_gives_an_empty_claims_sheet(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [rec("k", status="Partial")], {})
    assert table.sheets[1].rows == [] and claim_mismatches(table) == []


# ---- stats, shape, idempotence -----------------------------------------------------------------
def test_status_counts_and_table_shape(profile: WorkspaceProfile) -> None:
    records = [rec("a", status="Done", evidence_rows=1), rec("b", status="Done"),
               rec("c", status="Blocked"), rec("d", status="None found")]  # fmt: skip
    table = build_coverage(profile, records, {})
    assert (table.key, table.title) == ("coverage", "Coverage")
    assert table.main.columns == core.COVERAGE_COLUMNS
    assert table.stats["status_counts"] == {
        "Done": 1, "Partial": 1, "Not done": 0, "Blocked": 1, "None found": 1, "Not checked": 0
    }  # fmt: skip
    assert not table.internal


def test_empty_records_note(profile: WorkspaceProfile) -> None:
    table = build_coverage(profile, [], {})
    assert table.main.rows == [] and table.notes


def test_evidence_cell_forms() -> None:
    assert evidence_cell(rec("k", evidence_rows=1)) == "1 rows"
    assert evidence_cell(rec("k", source_codes=("S10", "S2"))) == "0 rows; S2, S10"
    assert evidence_cell(rec("k")) == core.NOT_FOUND


def test_idempotent_and_does_not_mutate(profile: WorkspaceProfile) -> None:
    records = [rec("a", "People", status="Done", evidence_rows=1), rec("b", status="Done")]
    claims = [claim("a"), claim("b")]
    tables = {"people": RepairedTable("people", "People", [Sheet("People", core.PEOPLE_COLUMNS)])}
    before = copy.deepcopy(tables)
    first = build_coverage(profile, records, tables, claims)
    second = build_coverage(profile, records, tables, claims)
    assert [s.rows for s in first.sheets] == [s.rows for s in second.sheets]
    assert first.stats == second.stats
    assert tables["people"].main.rows == before["people"].main.rows
