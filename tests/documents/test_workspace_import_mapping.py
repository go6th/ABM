"""The workspace import's mapping (``documents.workspace_import``), no database: a Jobs sheet
row becomes the posting the native tables keep (key by requisition, then URL, then title and
location; lanes from the platform lexicon; the class, sentences and summary kept); a People row
becomes a person with level, lane, priority and former employers; the Tech Stack rows sort into
the stack, the ranking and the teams; duplicates merge by the owner's keys with the counts kept.
SYNTHETIC rows only."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from documents.deliverables import Found
from documents.workspace_import import (
    TechRows,
    former_employers,
    keep_rows,
    merge_people,
    merge_postings,
    merge_tech,
    person_of,
    posting_of,
    tech_of,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
DOC = uuid4()
MEMBER = "ws/deliverables/Acme_Job_Postings.xlsx"


def _found(kind: str, value: str, seq: int = 1, **fields: str) -> Found:
    return Found(kind, value, fields, "quote", seq, {"member": MEMBER, "table": "t", "row": seq})


def test_workspace_rows_map_to_native_records_and_merge_duplicates() -> None:
    """See the module docstring."""
    description = "We run Salesforce Service Cloud. Experience with SAP S/4HANA is a plus."
    rows = [
        _found("jobs", "Salesforce Administrator", 1, requisition="R-1", lanes="Salesforce, SAP",
               status="Closed", posted="2026-03-01", last_seen="2026-05-01", location="Dublin",
               entity="Acme", seniority="mid_senior", **{"class": "Admin and owner role"},
               summary="Runs the Salesforce org for EMEA.",
               sentences=(
                   "Salesforce: We run Salesforce Service Cloud. || SAP: Experience with SAP"
                   " S/4HANA is a plus. || Veeva: (named in the title only)"
               ),
               description=description, url="https://jobs.example/a/R-1"),
        _found("jobs", "Salesforce Administrator", 2, requisition="R-1", lanes="Salesforce",
               status="Open", posted="2026-02-01", last_seen="2026-06-01", location="Madrid",
               entity="Acme", **{"class": "Admin and owner role"}, url="https://jobs.example/b/R-1"),
        _found("jobs", "Data Engineer", 3, lanes="Snowflake", status="Open", posted="2026-04-02",
               location="Pune", url="https://jobs.example/jobs/77?utm=x"),
        _found("jobs", "Warehouse Operator", 4, lanes="SAP", status="Closed", location="Sumter",
               entity="Waters (divested business)"),
        _found("jobs", "Warehouse Operator", 5, lanes="SAP", status="Closed", location="Sumter",
               entity="Waters (divested business)"),
    ]  # fmt: skip
    items = [posting_of(r, document_id=DOC, now=NOW) for r in rows]
    a = items[0].posting
    assert a.posting_key == "id:R-1" and not a.weak_key
    assert a.lanes == ("salesforce", "sap") and a.platforms == ("Salesforce", "SAP")
    assert a.status == "closed" and a.status_source == "feed" and a.window_bucket == "in_window"
    assert a.classification is not None and a.classification.label == "admin_owner"
    assert a.classification.evidence_sentence == "Runs the Salesforce org for EMEA."
    assert a.full_text_read and a.read and a.text_sha256 and a.text_complete
    assert [(s.platform, s.start) for s in a.sentences] == [("Salesforce", 0), ("SAP", 33)]
    assert a.sentences[1].end == 33 + len("Experience with SAP S/4HANA is a plus.")
    assert items[0].entity_tag == "Acme" and items[0].locations == ("Dublin",)
    assert items[2].posting.posting_key.startswith("url:")
    assert items[2].posting.lanes == ("data_ai",)  # Snowflake sits in the Data and AI lane
    assert items[3].posting.weak_key and items[3].posting.window_bucket == "undated"
    assert items[3].posting.posting_key == items[4].posting.posting_key

    merged, folded = merge_postings(items, [MEMBER] * 5)
    assert [m.posting.posting_key for m in merged] == [
        "id:R-1",
        items[2].posting.posting_key,
        items[3].posting.posting_key,
    ]
    assert folded == {MEMBER: 2}
    one = merged[0]
    assert one.posting.status == "open"  # Open when any copy is open
    assert (
        str(one.posting.first_seen_at) == "2026-02-01"
        and str(one.posting.last_seen) == "2026-06-01"
    )
    assert one.locations == ("Dublin", "Madrid") and one.merged_count == 1
    assert one.posting.platforms == ("Salesforce", "SAP")
    assert set(one.posting.urls) == {"https://jobs.example/a/R-1", "https://jobs.example/b/R-1"}
    assert merged[2].merged_count == 1 and merged[2].entity_tag == "Waters (divested business)"

    # the import itself folds nothing (exact copy, 2026-11-05): every sheet row is a row, and a
    # repeated key is told apart by its sheet row
    kept, none_folded = keep_rows(items, rows)
    assert [k.posting.posting_key for k in kept] == [
        "id:R-1",
        f"id:R-1#{rows[1].location['row']}",
        items[2].posting.posting_key,
        items[3].posting.posting_key,
        f"{items[3].posting.posting_key}#{rows[4].location['row']}",
    ]
    assert none_folded == {} and all(k.merged_count == 0 for k in kept)
    assert kept[1].posting.title == items[1].posting.title and kept[1].locations == ("Madrid",)

    # people: level and priority from the sheet, scope from the link, former employers parsed
    people_rows = [
        _found("people", "Avery Example", 1, title="Director, CRM Platforms", level="Director",
               lane="Salesforce and commercial", priority="A",
               link="https://www.linkedin.com/in/avery-example/", found_by="title search source 1",
               former_si="Deloitte (2017 to 2019); Slalom (2025 to 2026)", note="Owns the org."),
        _found("people", "Avery Example", 2, title="Director, CRM Platforms", level="Director",
               priority="A", link="https://www.linkedin.com/in/avery-example-2"),
        _found("people", "Blake Sample", 3, title="VP, Commercial Systems", level="VP",
               priority="B", link="https://www.linkedin.com/in/blake-sample"),
        _found("people", "Blake Sample", 4, title="VP, Commercial Systems", level="VP",
               priority="B"),
        _found("people", "Casey Placeholder", 5, title="Head of Digital",
               found_by="leadership page"),
    ]  # fmt: skip
    persons = [person_of(r, document_id=DOC) for r in people_rows]
    avery = persons[0]
    assert avery.person.classification.status == "in_scope"
    assert avery.person.classification.level == "director"
    assert avery.person.classification.priority == "A"
    assert avery.person.classification.lanes == ("Salesforce and commercial",)
    assert avery.person.origin == "title_search" and avery.note == "Owns the org."
    assert avery.former_employers == (("Deloitte", 2017, 2019), ("Slalom", 2025, 2026))
    assert former_employers("IBM; Accenture (2001 to 2003)") == (
        ("IBM", None, None),
        ("Accenture", 2001, 2003),
    )
    assert persons[4].person.classification.status == "review"
    assert persons[4].person.classification.level == "head"
    assert persons[4].person.origin == "mention"
    # only one profile link folds rows (exact copy, 2026-11-05): a second link with the same
    # name and title, and a row without a link, stay the sheet's own rows
    survivors, folded_people = merge_people(persons, [MEMBER] * 5)
    assert [p.person.name for p in survivors] == [
        "Avery Example",
        "Avery Example",
        "Blake Sample",
        "Blake Sample",
        "Casey Placeholder",
    ]
    assert folded_people == {} and all(p.merged_count == 0 for p in survivors)
    same_link = [
        person_of(
            _found("people", "Dana Twin", n, title="Director, IT",
                   link="https://www.linkedin.com/in/dana-twin/"),
            document_id=DOC,
        )
        for n in (6, 7)
    ]  # fmt: skip
    twins, folded_twins = merge_people(same_link, [MEMBER] * 2)
    assert len(twins) == 1 and twins[0].merged_count == 1 and folded_twins == {MEMBER: 1}

    # tech: stack, ranking and teams rows sorted and merged
    tech = TechRows()
    for r in [
        _found("tech", "Salesforce", 1, function="Sales", category="CRM", sources="1769"),
        _found("tech", "salesforce", 2, function="Sales", category="CRM", sources="1769"),
        _found("tech", "SAP", 3, rank="A", category="ERP", lane="SAP", sources="1,068",
               open_now="150", last_seen="2026-10-19", in_stack="Yes", checked="Yes"),
        _found("tech", "Platform team X", 4, category="team", function="Information Technology",
               sources="100", lanes="Salesforce, SAP", first_activity="2025-11-01",
               last_activity="2026-08-25", technologies="Salesforce (93); SAP (49)",
               locations="Wrocław (8); Republic of Poland (8)"),
        _found("tech", "platform team x", 5, category="team", sources="80"),
    ]:  # fmt: skip
        tech_of(r, tech)
    assert (len(tech.stack), len(tech.detections), len(tech.teams)) == (2, 1, 2)
    det = tech.detections[0]
    assert det.technology == "SAP" and det.source_count == 1068 and det.open_now == 150
    assert det.in_confirmed_stack is True and det.checked_by_reading is True
    assert det.last_seen_at == "2026-10-19"
    out, folded_tech = merge_tech(tech)
    assert (len(out.stack), len(out.detections), len(out.teams)) == (1, 1, 1)
    assert folded_tech == {MEMBER: 2}
    team = out.teams[0]
    assert team.postings_count == 100 and team.merged_count == 1
    assert str(team.first_activity) == "2025-11-01" and str(team.last_activity) == "2026-08-25"
    assert team.technologies == (("Salesforce", 93), ("SAP", 49))
    assert team.locations[0] == ("Wrocław", 8) and team.lane_technologies == ("Salesforce", "SAP")
