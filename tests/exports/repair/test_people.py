"""The People table and Leadership sheet. Two invented accounts prove nothing is account bound."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from exports.repair import core
from exports.repair.people import build_people
from exports.repair.records import LeadershipRecord, PersonRecord
from parity.platforms import default_platforms

RUN = date(2026, 10, 30)


def profile_a(**over: object) -> core.WorkspaceProfile:
    """Account A: Zephyr Dynamics, with a divested business and a subsidiary."""
    base = core.WorkspaceProfile(
        account_name="Zephyr Dynamics Inc.",
        account_short="Zephyr",
        domain="zephyrdyn.com",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
        domains=frozenset({"zephyrdyn.com", "zephyr-labs.io"}),
        aliases=("zd", "zephyr dynamics"),
        account_label="Zephyr",
        entities=(
            core.EntityRule("Quillon Instruments", (r"quillon",), counts_as_account=False),
            core.EntityRule("Zephyr Labs", (r"zephyr labs",), counts_as_account=True),
        ),
        lanes=("Salesforce", "SAP"),
        title_exclusions=("security", "cyber", "internal audit"),
        people_consent=True,
        platforms=default_platforms(),
    )
    return replace(base, **over)  # type: ignore[arg-type]


def profile_b(**over: object) -> core.WorkspaceProfile:
    """Account B: Lumora Foods, nothing in common with A."""
    base = core.WorkspaceProfile(
        account_name="Lumora Foods Ltd",
        account_short="Lumora",
        domain="lumorafoods.co.uk",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
        domains=frozenset({"lumorafoods.co.uk"}),
        aliases=("lumora",),
        entities=(),
        lanes=("Workday",),
        title_exclusions=("privacy officer",),
        people_consent=True,
        platforms=default_platforms(),
    )
    return replace(base, **over)  # type: ignore[arg-type]


def person(key: str, name: str, title: str = "Director of IT", **kw: object) -> PersonRecord:
    return PersonRecord(key=key, name=name, title=title, **kw)  # type: ignore[arg-type]


def rows(table: core.RepairedTable, sheet: str = "People") -> list[dict[str, str]]:
    return next(s for s in table.sheets if s.name == sheet).rows


def names_of(table: core.RepairedTable) -> list[str]:
    """The person names (an explained placeholder row is not a person)."""
    return [r["Name"] for r in rows(table) if not r["Name"].startswith("Not collected")]


def reasons(table: core.RepairedTable) -> list[str]:
    return [e.reason for e in table.exclusions]


# --------------------------------------------------------------------------- shape
def test_table_shape_and_columns() -> None:
    t = build_people(profile_a(), [person("p1", "Jane Rivera")], [])
    assert t.key == "people"
    assert t.title == "People"
    assert [s.name for s in t.sheets] == ["People", "Leadership"]
    assert t.sheets[0].columns == core.PEOPLE_COLUMNS
    assert t.sheets[1].columns == core.LEADERSHIP_COLUMNS
    assert not t.internal


def test_every_cell_filled_or_placeholder() -> None:
    lead = [LeadershipRecord("Tomas Varga", "CFO", source_url="https://zephyrdyn.com/x")]
    t = build_people(profile_a(), [person("p1", "Jane Rivera", title="")], lead)
    for sheet in t.sheets:
        for row in sheet.rows:
            assert set(row) == set(sheet.columns)
            for col, value in row.items():
                assert value.strip(), (sheet.name, col)


def test_minimal_person_row_values() -> None:
    t = build_people(profile_a(), [person("p1", "Jane Rivera", title="Director of IT")], [])
    row = rows(t)[0]
    assert row["Name"] == "Jane Rivera"
    assert row["Title as reported"] == "Director of IT"
    assert row["Level"] == "Director"
    assert row["Meets seniority rule"] == "Yes"
    assert row["Entity"] == "Zephyr"
    assert row["LinkedIn"] == core.NOT_FOUND
    assert row["Location"] == core.NOT_FOUND
    assert row["Found by"] == core.NOT_FOUND
    assert row["Former SI and consulting employers"] == core.NOT_FOUND
    assert row["Last verified"] == core.UNKNOWN
    assert row["Note"] == core.NOT_APPLICABLE
    assert row["Priority (A/B/C)"] in core.PRIORITIES


# --------------------------------------------------------------------------- (a) origin
def test_news_origin_never_a_people_row() -> None:
    recs = [
        person("n1", "Tomas Varga", origin="news_fact"),
        person("n2", "Hana Faris", origin="something_else"),
        person("p1", "Jane Rivera", origin="profile"),
    ]
    t = build_people(profile_a(), recs, [])
    assert names_of(t) == ["Jane Rivera"]
    assert reasons(t).count("sourced from news text") == 2
    assert {e.key for e in t.exclusions} == {"n1", "n2"}
    assert {e.table for e in t.exclusions} == {"people"}
    assert t.stats["dropped_news"] == 2


def test_import_and_people_search_origins_allowed() -> None:
    recs = [
        person("a", "Jane Rivera", origin="people_search"),
        person("b", "Tomas Varga", origin="import"),
    ]
    assert set(names_of(build_people(profile_b(), recs, []))) == {"Jane Rivera", "Tomas Varga"}


# --------------------------------------------------------------------------- (b) consent
@pytest.mark.parametrize("make", [profile_a, profile_b])
def test_no_consent_no_rows_gives_one_placeholder_row(make) -> None:  # type: ignore[no-untyped-def]
    t = build_people(make(people_consent=False), [], [])
    people = rows(t)
    assert len(people) == 1
    assert people[0]["Name"] == core.not_collected("people data consent needed")
    others = {v for k, v in people[0].items() if k != "Name"}
    assert others == {core.NOT_APPLICABLE}
    assert set(people[0]) == set(core.PEOPLE_COLUMNS)
    assert t.stats["consent_missing"] is True
    assert t.stats["total"] == 0
    assert t.notes
    assert "consent" in t.notes[0].lower()


def test_no_consent_news_only_rows_still_placeholder() -> None:
    t = build_people(
        profile_a(people_consent=False), [person("n1", "Tomas Varga", origin="news_fact")], []
    )
    assert len(rows(t)) == 1
    assert t.stats["consent_missing"] is True


def test_no_consent_but_profile_rows_exist_builds_table() -> None:
    t = build_people(profile_a(people_consent=False), [person("p1", "Jane Rivera")], [])
    assert names_of(t) == ["Jane Rivera"]
    assert t.stats["consent_missing"] is False
    assert not t.notes


def test_consent_given_and_empty_has_one_explained_row_and_zero_total() -> None:
    t = build_people(profile_a(), [], [])
    assert len(rows(t)) == 1
    assert rows(t)[0]["Name"].startswith("Not collected: ")
    assert t.stats["consent_missing"] is False
    assert t.stats["total"] == 0


def test_consent_missing_never_pads() -> None:
    t = build_people(profile_b(people_consent=False), [], [])
    assert len(rows(t)) == 1


# --------------------------------------------------------------------------- (c) names
def test_invalid_names_excluded_with_reason() -> None:
    recs = [
        person("p1", "Jane Rivera"),
        person("p2", "Director of IT"),
        person("p3", "I think the market will grow"),
        person("p4", "[Name]"),
        person("p5", "Acme Corp"),
        person("p6", "Tomas"),
    ]
    t = build_people(profile_a(), recs, [])
    assert names_of(t) == ["Jane Rivera"]
    assert reasons(t) == ["not a valid person name"] * 5
    assert t.stats["dropped_invalid_name"] == 5


def test_descriptive_phrase_is_parsed_into_name_and_title() -> None:
    recs = [
        person("p1", "Tomas Varga, chief digital officer of Zephyr", title=""),
        person("p2", "Hana Faris serves as senior director of data", title="Employee"),
        person("p3", "Pieter van der Berg - Head of Procurement", title="Director of IT"),
    ]
    t = build_people(profile_a(), recs, [])
    by_name = {r["Name"]: r for r in rows(t)}
    assert by_name["Tomas Varga"]["Title as reported"] == "chief digital officer of Zephyr"
    assert by_name["Tomas Varga"]["Level"] == "C-level, EVP and SVP"
    assert by_name["Hana Faris"]["Title as reported"] == "senior director of data"
    assert by_name["Hana Faris"]["Level"] == "Senior Director"
    # a real stored title is kept; the phrase remainder does not override it
    assert by_name["Pieter van der Berg"]["Title as reported"] == "Director of IT"


def test_headline_used_when_title_empty() -> None:
    t = build_people(
        profile_a(), [person("p1", "Jane Rivera", title="", headline="Head of Applications")], []
    )
    assert rows(t)[0]["Title as reported"] == "Head of Applications"
    assert rows(t)[0]["Level"] == "Head"


def test_empty_title_everywhere_cannot_meet_the_rule_and_is_dropped() -> None:
    t = build_people(profile_a(), [person("p1", "Jane Rivera", title="")], [])
    assert t.stats["dropped_below_rule"] == 1
    assert [x.reason for x in t.exclusions] == [
        "below the seniority rule and the title names no platform"
    ]


def test_non_latin_and_accented_names_survive() -> None:
    t = build_people(profile_b(), [person("p1", "José Müller"), person("p2", "Łukasz Nowak")], [])
    assert set(names_of(t)) == {"José Müller", "Łukasz Nowak"}


# --------------------------------------------------------------------------- (d) de-dupe
def test_dedupe_on_profile_url() -> None:
    a = person("p1", "Jane Rivera", profile_url="https://www.linkedin.com/in/jane-rivera/?utm_x=1")
    b = person("p2", "Jane Riviera", profile_url="https://uk.linkedin.com/in/jane-rivera")
    t = build_people(profile_a(), [a, b], [])
    assert names_of(t) == ["Jane Rivera"]
    assert t.stats["dropped_duplicates"] == 1
    assert reasons(t) == ["duplicate of p1"]
    assert t.exclusions[0].key == "p2"


def test_dedupe_on_name_and_employer() -> None:
    a = person("p1", "Jane Rivera", employer="Zephyr Dynamics, Inc.")
    b = person("p2", "Jane  Rivera", employer="zephyr dynamics")  # spaces / case / suffix
    c = person("p3", "Jane Rivera", employer="Other Employer")
    t = build_people(profile_a(), [a, b, c], [])
    assert names_of(t) == ["Jane Rivera", "Jane Rivera"]  # a+b merged, c kept
    assert t.stats["dropped_duplicates"] == 1


def test_dedupe_is_accent_insensitive() -> None:
    a = person("p1", "José Müller", employer="X")
    b = person("p2", "Jose Muller", employer="X")
    assert len(rows(build_people(profile_a(), [a, b], []))) == 1


def test_merge_keeps_non_empty_fields_best_priority_union_lanes() -> None:
    url = "https://www.linkedin.com/in/jane-rivera"
    a = person("p1", "Jane Rivera", profile_url=url, priority="C", lanes=("SAP",), location="")
    b = person(
        "p2", "Jane Rivera", profile_url=url, priority="A", lanes=("Salesforce", "SAP"),
        location="Austin", found_by="people_search", note="met at event",
        last_verified=date(2026, 9, 1),
    )  # fmt: skip
    row = rows(build_people(profile_a(), [a, b], []))[0]
    assert row["Priority (A/B/C)"] == "A"
    assert row["Lane"] == "SAP; Salesforce"
    assert row["Location"] == "Austin"
    assert row["Found by"] == "people_search"
    assert row["Note"] == "met at event"
    assert row["Last verified"] == "2026-09-01"


def test_transitive_merge_url_then_name() -> None:
    a = person("p1", "Jane Rivera", profile_url="https://www.linkedin.com/in/jr", employer="X")
    b = person("p2", "Jane Rivera", employer="X")
    c = person("p3", "J. Rivera", profile_url="https://www.linkedin.com/in/jr", employer="Y")
    t = build_people(profile_a(), [a, b, c], [])
    assert len(rows(t)) == 1
    assert t.stats["dropped_duplicates"] == 2


def test_same_name_different_people_without_urls_and_employers_merge_only_on_equal_employer() -> (
    None
):
    a = person("p1", "Jane Rivera", employer="Alpha")
    b = person("p2", "Jane Rivera", employer="Beta")
    assert len(rows(build_people(profile_a(), [a, b], []))) == 2


# --------------------------------------------------------------------------- (e) employer
def test_other_employer_domain_dropped_and_counted() -> None:
    recs = [
        person("p1", "Jane Rivera", employer_domain="zephyrdyn.com"),
        person("p2", "Tomas Varga", employer_domain="www.ZephyrDyn.com"),
        person("p3", "Hana Faris", employer_domain="other-company.com"),
        person("p4", "Pieter van der Berg", employer_domain="https://jobs.other-company.com/x"),
        person("p5", "Karim bin Salem", employer_domain=""),
        person("p6", "Maria de la Cruz", employer_domain="careers.zephyr-labs.io"),
    ]
    t = build_people(profile_a(), recs, [])
    assert set(names_of(t)) == {"Jane Rivera", "Tomas Varga", "Karim bin Salem", "Maria de la Cruz"}
    assert t.stats["dropped_other_employer"] == 2
    assert {e.key for e in t.exclusions} == {"p3", "p4"}


def test_domain_rule_depends_on_the_profile() -> None:
    recs = [person("p1", "Jane Rivera", employer_domain="zephyrdyn.com")]
    assert len(rows(build_people(profile_a(), recs, []))) == 1
    t = build_people(profile_b(), recs, [])
    assert t.stats["dropped_other_employer"] == 1
    assert rows(t)[0]["Name"].startswith("Not collected")


def test_registrable_match_for_two_label_suffix() -> None:
    recs = [person("p1", "Jane Rivera", employer_domain="careers.lumorafoods.co.uk")]
    assert names_of(build_people(profile_b(), recs, [])) == ["Jane Rivera"]
    other = [person("p2", "Jane Rivera", employer_domain="careers.other.co.uk")]
    assert build_people(profile_b(), other, []).stats["dropped_other_employer"] == 1


# --------------------------------------------------------------------------- (f) exclusions
def test_profile_title_exclusions_whole_word_case_insensitive() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Cyber Defence"),
        person("p2", "Tomas Varga", title="Head of INTERNAL  AUDIT"),
        person("p3", "Hana Faris", title="Vice President, Security"),
        person("p4", "Pieter van der Berg", title="Director of Insecurity Research"),
        person("p5", "Karim bin Salem", title="Director of Data"),
    ]
    t = build_people(profile_a(), recs, [])
    assert set(names_of(t)) == {"Pieter van der Berg", "Karim bin Salem"}
    assert t.stats["dropped_title_excluded"] == 3
    assert all(r.startswith("title excluded: ") for r in reasons(t))


def test_exclusions_are_per_profile() -> None:
    rec = [person("p1", "Jane Rivera", title="Director of Security")]
    assert names_of(build_people(profile_a(), rec, [])) == []  # excluded in A
    assert names_of(build_people(profile_b(), rec, [])) == ["Jane Rivera"]  # allowed in B
    priv = [person("p1", "Jane Rivera", title="Privacy Officer")]
    assert names_of(build_people(profile_b(), priv, [])) == []


def test_flagged_rows_excluded() -> None:
    recs = [
        person("p1", "Jane Rivera", excluded=True),
        person("p2", "Tomas Varga", internal_only=True),
        person("p3", "Hana Faris"),
    ]
    t = build_people(profile_a(), recs, [])
    assert names_of(t) == ["Hana Faris"]
    assert reasons(t) == ["marked excluded", "internal only"]
    assert t.stats["dropped_flagged"] == 2


# --------------------------------------------------------------------------- (g) level, rule
@pytest.mark.parametrize(
    ("title", "level", "meets"),
    [
        ("Chief Information Officer", "C-level, EVP and SVP", "Yes"),
        ("Vice President of Sales Operations", "VP", "Yes"),
        ("Senior Director, Applications", "Senior Director", "Yes"),
        ("Director of Finance", "Director", "Yes"),
        ("Associate Director, Planning", "Associate Director", "Yes"),
        ("Head of Procurement", "Head", "Yes"),
        ("Senior Manager, Programs", "Senior Manager", "Yes"),
        ("Manager, Salesforce Facilities", "Manager", "No"),
        ("Analyst, SAP Reporting", "Lead, owner, specialist or developer", "No"),
    ],
)
def test_level_and_seniority_rule(title: str, level: str, meets: str) -> None:
    row = rows(build_people(profile_a(), [person("p1", "Jane Rivera", title=title)], []))[0]
    assert row["Level"] == level
    assert row["Meets seniority rule"] == meets


def test_below_the_rule_stays_only_when_the_title_names_a_platform() -> None:
    """The column is the level alone (No below Senior Manager); a person below it is listed
    only when the title names a lane platform, otherwise the row is dropped and logged."""
    recs = [
        person("p1", "Jane Rivera", title="Manager, Salesforce Administration"),
        person("p2", "Tomas Varga", title="Developer, SAP Basis"),
        person("p3", "Hana Faris", title="Manager, Facilities"),
        person("p4", "Omar Reyes", title="Director of Facilities"),
    ]
    t = build_people(profile_a(), recs, [])
    by = {r["Name"]: r for r in rows(t)}
    assert by["Jane Rivera"]["Meets seniority rule"] == "No"
    assert by["Tomas Varga"]["Meets seniority rule"] == "No"
    assert by["Omar Reyes"]["Meets seniority rule"] == "Yes"
    assert "Hana Faris" not in by and t.stats["dropped_below_rule"] == 1


def test_stored_level_used_only_when_title_is_silent() -> None:
    silent = person("p1", "Jane Rivera", title="Operations Partner", level="Director")
    loud = person("p2", "Tomas Varga", title="Head of Operations", level="Manager")
    by = {r["Name"]: r for r in rows(build_people(profile_b(), [silent, loud], []))}
    assert by["Jane Rivera"]["Level"] == "Director"
    assert by["Tomas Varga"]["Level"] == "Head"


def test_priority_from_record_else_rule_else_c() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Salesforce", priority="c"),
        person("p2", "Tomas Varga", title="Director of Salesforce", priority=""),
        person("p3", "Hana Faris", title="Director of Facilities", priority="Z"),
        person("p4", "Pieter van der Berg", title="Head of Facilities", priority=""),
    ]
    by = {r["Name"]: r for r in rows(build_people(profile_a(), recs, []))}
    assert by["Jane Rivera"]["Priority (A/B/C)"] == "C"
    assert by["Tomas Varga"]["Priority (A/B/C)"] == "A"
    assert by["Hana Faris"]["Priority (A/B/C)"] in ("B", "C")
    assert by["Pieter van der Berg"]["Priority (A/B/C)"] in ("B", "C")


def test_lane_from_record_else_rules() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of IT", lanes=("My lane",)),
        person("p2", "Tomas Varga", title="Director of Salesforce"),
    ]
    by = {r["Name"]: r for r in rows(build_people(profile_a(), recs, []))}
    assert by["Jane Rivera"]["Lane"] == "My lane"
    assert by["Tomas Varga"]["Lane"] == "Salesforce and commercial"


# --------------------------------------------------------------------------- (h) cells
def test_linkedin_rules() -> None:
    recs = [
        person("p1", "Jane Rivera", profile_url="https://www.linkedin.com/in/jane-rivera/?trk=x"),
        person("p2", "Tomas Varga", profile_url="https://uk.linkedin.com/pub/tomas-varga/1/2/3"),
        person("p3", "Hana Faris", profile_url="https://www.linkedin.com/company/zephyr"),
        person("p4", "Pieter van der Berg", profile_url="https://example.org/in/pieter"),
        person("p5", "Karim bin Salem", profile_url=""),
        person("p6", "Maria de la Cruz", profile_url="https://notlinkedin.com/in/maria"),
    ]
    by = {r["Name"]: r["LinkedIn"] for r in rows(build_people(profile_a(), recs, []))}
    assert by["Jane Rivera"] == "https://uk.linkedin.com/in/jane-rivera".replace("uk.", "")
    assert by["Tomas Varga"] == "https://uk.linkedin.com/pub/tomas-varga/1/2/3"
    for n in ("Hana Faris", "Pieter van der Berg", "Karim bin Salem", "Maria de la Cruz"):
        assert by[n] == core.NOT_FOUND


def test_optional_cells_value_or_placeholder() -> None:
    rec = person(
        "p1", "Jane Rivera", location="Austin, US", found_by="people_search",
        former_si="Firmco (2015 to 2019)", note="n", last_verified=date(2026, 8, 1),
    )  # fmt: skip
    row = rows(build_people(profile_a(), [rec], []))[0]
    assert row["Location"] == "Austin, US"
    assert row["Found by"] == "people_search"
    assert row["Former SI and consulting employers"] == "Firmco (2015 to 2019)"
    assert row["Note"] == "n"
    assert row["Last verified"] == "2026-08-01"


def test_entity_resolution_by_domain_text_and_default() -> None:
    recs = [
        person(
            "p1", "Jane Rivera", employer_domain="zephyrdyn.com", employer="Quillon Instruments"
        ),
        person("p2", "Tomas Varga", employer="Quillon Instruments GmbH"),
        person("p3", "Hana Faris", employer="Zephyr Labs"),
        person("p4", "Pieter van der Berg", employer="Some Other Text"),
        person("p5", "Karim bin Salem"),
    ]
    by = {r["Name"]: r["Entity"] for r in rows(build_people(profile_a(), recs, []))}
    assert by["Jane Rivera"] == "Zephyr"  # the domain wins
    assert by["Tomas Varga"] == "Quillon Instruments"
    assert by["Hana Faris"] == "Zephyr Labs"
    assert by["Pieter van der Berg"] == "Zephyr"
    assert by["Karim bin Salem"] == "Zephyr"


def test_entity_label_falls_back_to_short_name() -> None:
    row = rows(build_people(profile_b(), [person("p1", "Jane Rivera")], []))[0]
    assert row["Entity"] == "Lumora"


def test_divested_entity_rows_do_not_count() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of IT"),
        person("p2", "Tomas Varga", title="Director of IT", employer="Quillon Instruments"),
        person("p3", "Hana Faris", title="Director of IT", employer="Zephyr Labs"),
    ]
    t = build_people(profile_a(), recs, [])
    assert len(rows(t)) == 3
    assert t.stats["non_account_entity"] == 1
    assert t.stats["total"] == 2
    assert t.stats["meets_seniority"] == 2


def test_placeholder_values_are_not_treated_as_data() -> None:
    rec = person(
        "p1", "Jane Rivera", location="Not found", note="Not applicable", title="Unknown",
        headline="Director of IT",
    )  # fmt: skip
    row = rows(build_people(profile_a(), [rec], []))[0]
    assert row["Location"] == core.NOT_FOUND
    assert row["Note"] == core.NOT_APPLICABLE
    assert row["Title as reported"] == "Director of IT"


# --------------------------------------------------------------------------- (i) sort, stats
def test_sort_priority_then_level_then_name_stable() -> None:
    recs = [
        person("p1", "Zed Alpha", title="Manager, Salesforce", priority="A"),
        person("p2", "Ben Zulu", title="Director of Sales", priority="B"),
        person("p3", "Amy Yankee", title="Director of Sales", priority="A"),
        person("p4", "Cal Xray", title="Chief Sales Officer", priority="A"),
        person("p5", "Dee Whiskey", title="Director of Sales", priority="A"),
        person("p6", "Eve Victor", title="Head of Sales", priority="C"),
    ]
    got = names_of(build_people(profile_a(), recs, []))
    assert got == ["Cal Xray", "Amy Yankee", "Dee Whiskey", "Zed Alpha", "Ben Zulu", "Eve Victor"]


def test_stats_counts() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Salesforce", priority="A"),
        person("p2", "Tomas Varga", title="Manager, Salesforce Facilities", priority="A"),
        person("p3", "Hana Faris", title="Head of Data", priority="B"),
        person("p4", "Pieter van der Berg", title="Manager, SAP", priority="A"),
        person("p5", "Bad", title="Director"),
    ]
    t = build_people(profile_a(), recs, [])
    s = t.stats
    assert s["total"] == 4
    assert s["meets_seniority"] == 2  # Director and Head; the two Managers name a platform only
    assert s["priority_a_senior"] == 1
    assert s["per_level"]["Director"] == 1
    assert s["per_level"]["Head"] == 1
    assert s["per_level"]["Manager"] == 2
    assert sum(s["per_level"].values()) == s["total"]
    assert list(s["per_level"]) == [
        "C-level, EVP and SVP", "VP", "Senior Director", "Director", "Associate Director",
        "Head", "Senior Manager", "Manager", "Lead, owner, specialist or developer",
    ]  # fmt: skip
    assert s["consent_missing"] is False
    assert s["dropped_invalid_name"] == 1
    for key in ("dropped_news", "dropped_other_employer", "dropped_title_excluded"):
        assert s[key] == 0


# --------------------------------------------------------------------------- Leadership
def lead(name: str, role: str = "Chief Financial Officer", **kw: object) -> LeadershipRecord:
    kw.setdefault("source_url", "https://www.zephyrdyn.com/news/release-1")
    return LeadershipRecord(name=name, role=role, **kw)  # type: ignore[arg-type]


def test_leadership_empty_input_gives_one_row() -> None:
    t = build_people(profile_a(), [], [])
    sheet = rows(t, "Leadership")
    assert len(sheet) == 1
    assert sheet[0]["Name"] == core.not_collected(
        "no press releases or filings were read for leadership"
    )
    assert {v for k, v in sheet[0].items() if k != "Name"} == {core.NOT_APPLICABLE}
    assert t.stats["leadership_total"] == 0


def test_leadership_status_dates_and_columns() -> None:
    recs = [
        lead("Tomas Varga", status="current", start=date(2020, 1, 6)),
        lead("Hana Faris", status="Moved to divested business", end=date(2026, 2, 1)),
        lead("Karim bin Salem", end=date(2026, 3, 1)),  # blank status, ended
        lead("Maria de la Cruz", end=date(2027, 1, 1)),  # blank status, future end
        lead("Jane Rivera", status="Retired??"),  # unknown word
        lead("Pieter van der Berg", status="Departed"),
    ]
    sheet = {r["Name"]: r for r in rows(build_people(profile_a(), [], recs), "Leadership")}
    tv = sheet["Tomas Varga"]
    assert tv["Status"] == "Current"
    assert tv["Start date"] == "2020-01-06"
    assert tv["End or retire date"] == core.NOT_APPLICABLE
    assert tv["Source type"] == core.UNKNOWN
    assert tv["Note"] == core.NOT_APPLICABLE
    assert sheet["Hana Faris"]["Status"] == "Moved to divested business"
    assert sheet["Hana Faris"]["End or retire date"] == "2026-02-01"
    assert sheet["Hana Faris"]["Start date"] == core.UNKNOWN
    assert sheet["Karim bin Salem"]["Status"] == "Departed"
    assert sheet["Maria de la Cruz"]["Status"] == "Unverified"
    assert sheet["Maria de la Cruz"]["End or retire date"] == "2027-01-01"
    assert sheet["Jane Rivera"]["Status"] == "Unverified"
    assert sheet["Jane Rivera"]["End or retire date"] == core.UNKNOWN
    assert sheet["Pieter van der Berg"]["End or retire date"] == core.UNKNOWN
    assert all(r["Status"] in core.LEADERSHIP_STATUSES for r in sheet.values())


def test_leadership_end_date_today_counts_as_departed() -> None:
    sheet = rows(build_people(profile_a(), [], [lead("Tomas Varga", end=RUN)]), "Leadership")
    assert sheet[0]["Status"] == "Departed"


def test_leadership_run_date_drives_departed_not_the_clock() -> None:
    rec = [lead("Tomas Varga", end=date(2026, 12, 1))]
    early = rows(build_people(profile_a(), [], rec), "Leadership")[0]["Status"]
    late = rows(build_people(profile_a(run_date=date(2027, 1, 1)), [], rec), "Leadership")[0]
    assert early == "Unverified"
    assert late["Status"] == "Departed"


def test_leadership_invalid_names_and_missing_source_dropped() -> None:
    recs = [
        lead("Tomas Varga"),
        lead("Chief Financial Officer"),
        lead("Hana Faris", source_url=""),
        lead("Karim bin Salem", source_url="not a url"),
    ]
    t = build_people(profile_a(), [], recs)
    assert [r["Name"] for r in rows(t, "Leadership")] == ["Tomas Varga"]
    assert t.stats["leadership_dropped"] == 3
    assert reasons(t).count("not a valid person name") == 1
    assert reasons(t).count("no source URL") == 2
    assert t.stats["leadership_total"] == 1


def test_leadership_all_dropped_still_explains() -> None:
    t = build_people(profile_a(), [], [lead("Hana Faris", source_url="")])
    row = rows(t, "Leadership")[0]
    assert row["Name"].startswith("Not collected: ")
    assert t.stats["leadership_total"] == 0


def test_leadership_dedupe_by_name_and_role_merges_fields() -> None:
    recs = [
        lead("Tomas Varga", "Chief Financial Officer", status="Current"),
        lead("Tomas  Varga", "chief financial officer", start=date(2021, 5, 1), note="x",
             source_type="Press release"),
        lead("Tomas Varga", "Chief Operating Officer"),
    ]  # fmt: skip
    t = build_people(profile_a(), [], recs)
    sheet = rows(t, "Leadership")
    assert len(sheet) == 2
    cfo = sheet[0]
    assert cfo["Start date"] == "2021-05-01"
    assert cfo["Note"] == "x"
    assert cfo["Source type"] == "Press release"
    assert t.stats["leadership_duplicates"] == 1


def test_leadership_descriptive_name_parsed_role_from_phrase() -> None:
    rec = [
        LeadershipRecord(
            name="Hana Faris serves as chief operating officer of Zephyr",
            role="",
            source_url="https://zephyrdyn.com/a",
        )
    ]
    row = rows(build_people(profile_a(), [], rec), "Leadership")[0]
    assert row["Name"] == "Hana Faris"
    assert row["Role"] == "chief operating officer of Zephyr"


def test_leadership_stats_per_status() -> None:
    recs = [
        lead("Tomas Varga", status="Current"),
        lead("Hana Faris", status="Departed"),
        lead("Karim bin Salem", status="Unverified"),
        lead("Maria de la Cruz", status="Moved to divested business"),
    ]
    s = build_people(profile_a(), [], recs).stats
    assert s["leadership_total"] == 4
    assert s["leadership_current"] == 1
    assert s["leadership_departed"] == 1
    assert s["leadership_unverified"] == 1
    assert s["leadership_moved_to_divested_business"] == 1


def test_departed_leader_flags_person_note_only() -> None:
    people = [
        person("p1", "Tomas Varga", title="Director of IT"),
        person("p2", "Hana Faris", title="Director of IT", note="seen twice"),
        person("p3", "Karim bin Salem", title="Director of IT"),
    ]
    leaders = [
        lead("Tomas Varga", status="Departed", source_url="https://www.press.example.org/a/b"),
        lead("Hana Faris", status="Departed", source_url="https://filings.example.net/x"),
        lead("Hana Faris", "Chief Executive Officer", status="Current"),  # still current elsewhere
        lead("Karim bin Salem", status="Current"),
    ]
    t = build_people(profile_a(), people, leaders)
    by = {r["Name"]: r for r in rows(t)}
    assert by["Tomas Varga"]["Note"] == "Departed per press.example.org"
    assert by["Tomas Varga"]["Meets seniority rule"] == "Yes"  # unchanged
    assert by["Hana Faris"]["Note"] == "seen twice"  # also a current executive: no flag
    assert by["Karim bin Salem"]["Note"] == core.NOT_APPLICABLE
    assert t.stats["departed_flagged"] == 1


def test_departed_note_appended_to_existing_note_once() -> None:
    people = [person("p1", "Tomas Varga", note="seen twice")]
    leaders = [lead("Tomas Varga", status="Departed", source_url="https://news.example.org/a")]
    first = build_people(profile_a(), people, leaders)
    assert rows(first)[0]["Note"] == "seen twice; Departed per news.example.org"
    again = build_people(
        profile_a(), [person("p1", "Tomas Varga", note=rows(first)[0]["Note"])], leaders
    )
    assert rows(again)[0]["Note"] == "seen twice; Departed per news.example.org"


def test_leadership_built_without_people_consent() -> None:
    t = build_people(profile_a(people_consent=False), [], [lead("Tomas Varga", status="Current")])
    assert rows(t)[0]["Name"].startswith("Not collected: people data consent")
    assert rows(t, "Leadership")[0]["Name"] == "Tomas Varga"


# --------------------------------------------------------------------------- neutrality, purity
def test_two_accounts_get_independent_results() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Security", employer_domain="zephyrdyn.com"),
        person("p2", "Tomas Varga", title="Director of Privacy Officer",
               employer_domain="lumorafoods.co.uk"),
        person("p3", "Hana Faris", title="Head of Workday", employer="Quillon Instruments"),
    ]  # fmt: skip
    a = build_people(profile_a(), recs, [])
    b = build_people(profile_b(), recs, [])
    assert set(names_of(a)) == {"Hana Faris"}
    assert rows(a)[0]["Entity"] == "Quillon Instruments"
    assert names_of(b) == ["Hana Faris"]
    assert rows(b)[0]["Entity"] == "Lumora"  # B has no Quillon rule
    # B's own domain keeps its employee, A's domain row is another employer there
    assert b.stats["dropped_other_employer"] == 1
    assert b.stats["dropped_title_excluded"] == 1


def test_source_has_no_account_names() -> None:
    import re
    from pathlib import Path

    import exports.repair.names as names_mod
    import exports.repair.people as people_mod

    for mod in (names_mod, people_mod):
        text = Path(mod.__file__).read_text(encoding="utf-8").lower()
        assert not re.search(r"becton|waters|salesforce|\bbd\b|mulesoft|veeva", text)


def test_build_people_is_idempotent_and_does_not_mutate_inputs() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Salesforce", profile_url="https://www.linkedin.com/in/jr"),
        person("p2", "Jane Rivera", profile_url="https://www.linkedin.com/in/jr/"),
        person("p3", "Tomas Varga, chief digital officer", title=""),
        person("p4", "Nope", title="x"),
    ]  # fmt: skip
    leaders = [lead("Tomas Varga", status="Departed")]
    before = (list(recs), list(leaders))
    one = build_people(profile_a(), recs, leaders)
    two = build_people(profile_a(), recs, leaders)
    assert (recs, leaders) == before
    assert [s.rows for s in one.sheets] == [s.rows for s in two.sheets]
    assert one.stats == two.stats
    assert one.exclusions == two.exclusions


def test_output_fed_back_gives_the_same_table() -> None:
    recs = [
        person("p1", "Jane Rivera", title="Director of Salesforce", priority="A", lanes=("SAP",),
               profile_url="https://www.linkedin.com/in/jr", location="Austin"),
        person("p2", "Tomas Varga", title="Manager, Facilities", priority="C"),
        person("p3", "Hana Faris", title="Head of Data", priority="B", found_by="people_search"),
    ]  # fmt: skip
    first = build_people(profile_a(), recs, [])
    again = [
        PersonRecord(
            key=f"r{i}",
            name=r["Name"],
            title=r["Title as reported"],
            level=r["Level"],
            lanes=(r["Lane"],),
            priority=r["Priority (A/B/C)"],
            profile_url=r["LinkedIn"],
            location=r["Location"],
            found_by=r["Found by"],
            former_si=r["Former SI and consulting employers"],
            note=r["Note"],
        )
        for i, r in enumerate(rows(first))
    ]
    second = build_people(profile_a(), again, [])
    assert rows(second) == rows(first)
    assert second.stats["total"] == first.stats["total"]


def test_large_input_keeps_every_row_valid() -> None:
    first = ["Ana", "Ben", "Cal", "Dee", "Eve", "Fay", "Gus", "Hal"]
    last = ["Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel"]
    recs = [
        person(f"p{i}", f"{first[i % 8]} {last[(i // 8) % 8]}", title="Director of IT",
               employer=f"E{i}")
        for i in range(64)
    ]  # fmt: skip
    t = build_people(profile_b(), recs, [])
    assert len(rows(t)) == 64
    assert t.stats["total"] == 64
    assert t.stats["meets_seniority"] == 64
