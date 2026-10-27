"""Every rule on synthetic inputs, and the reference numbers recomputed from the reference
workspace's raw rows when that workspace is present."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import pytest

from parity import rules
from parity.platforms import LanePattern, Platforms, default_platforms, platforms_from_data
from tests.parity.conftest import REFERENCE_WS, reference


@pytest.fixture(scope="module")
def platforms() -> Platforms:
    return default_platforms()


def test_dictionary_shape(platforms: Platforms) -> None:
    assert platforms.lane_names == (
        "Salesforce",
        "MuleSoft",
        "ServiceNow",
        "SAP",
        "Tableau",
        "Veeva",
        "Workday",
    )
    assert len(platforms.ranking) == 31
    assert platforms.out_of_scope == ("Databricks", "Siebel", "ServiceMax")
    sap = rules.lane_pattern(platforms, "SAP")
    assert sap.case_sensitive and not rules.lane_pattern(platforms, "Veeva").case_sensitive


def test_dictionary_refuses_an_out_of_scope_platform() -> None:
    with pytest.raises(ValueError):
        platforms_from_data(
            {
                "lanes": [],
                "ranking": [{"name": "Siebel", "category": "CRM", "pattern": "siebel"}],
                "out_of_scope": ["Siebel"],
            }
        )


def test_sap_is_matched_case_sensitively(platforms: Platforms) -> None:
    assert "SAP" in rules.match_platforms("Analyst", "Works in SAP ECC.", platforms)
    assert "SAP" not in rules.match_platforms("Analyst", "Fresh sap from the tree.", platforms)
    assert "SAP" in rules.match_platforms("Buyer", "Experience with Ariba required.", platforms)
    assert "Salesforce" in rules.match_platforms("Rep", "uses SALESFORCE daily", platforms)


def test_title_only_match_and_skip(platforms: Platforms) -> None:
    found = rules.match_platforms("Tableau Developer", "Builds dashboards.", platforms)
    assert found == {"Tableau": [rules.TITLE_ONLY]}
    assert rules.match_platforms("Tableau Developer", "x", platforms, skip={"Tableau"}) == {}


def test_platform_sentences_boundaries_and_limit() -> None:
    text = (
        "Intro. * Uses Veeva Vault daily * Also Veeva CRM. Veeva again here. "
        "Fourth Veeva mention. Fifth Veeva mention."
    )
    out = rules.platform_sentences(text, r"veeva", re.IGNORECASE)
    assert out[0] == "Uses Veeva Vault daily * Also Veeva CRM."
    assert len(out) == 4
    assert rules.platform_sentences("No end  with   Veeva", r"veeva", re.I) == ["No end with Veeva"]


def test_join_sentences_cleans_and_cuts() -> None:
    joined = rules.join_sentences({"SAP": ["a – b", "c"], "Veeva": ["**d**"]})
    assert joined == "SAP: a, b | c || Veeva: d"
    assert len(rules.join_sentences({"SAP": ["x" * 5000]})) == 3000


def test_classify_precedence() -> None:
    assert rules.classify(("Build role", "why"), {}) == ("Build role", "why")
    assert rules.classify(None, {"Salesforce": ["Logs in Field Service Lightning."]}) == (
        "Working tool",
        rules.FIELD_SERVICE_WHAT,
    )
    assert rules.classify(None, {"SAP": ["Experience with SAP."]}) == ("Working tool", "")
    assert rules.classify(None, {"SAP": ["SAP."]}) == ("User role", "")


def test_tag_entity_rules_are_data() -> None:
    entities = [
        rules.EntityRule("Acme", True),
        rules.EntityRule("Widgetco (divested)", False, (r"Widgetco",)),
        rules.EntityRule("Old unit (divested)", False, (r"Old Unit",), before="2026-02-09"),
        rules.EntityRule("Acme (Subsidiary)", True, (r"Subsidiary Co",)),
    ]
    assert (
        rules.tag_entity("Role", "join Widgetco", "2026-05-01", entities) == "Widgetco (divested)"
    )
    assert rules.tag_entity("Role", "widgetco", "2026-05-01", entities) == "Acme"
    assert rules.tag_entity("Old Unit role", "", "2026-01-01", entities) == "Old unit (divested)"
    assert rules.tag_entity("Old Unit role", "", "2026-02-09", entities) == "Acme"
    assert (
        rules.tag_entity("Role", "at Subsidiary Co", "2026-03-01", entities) == "Acme (Subsidiary)"
    )
    assert rules.tag_entity("Role", "", "2026-03-01", entities[1:2]) == "Widgetco (divested)"


def test_jobs_counts_count_the_account_only() -> None:
    rows = [
        {"entity": "Acme", "platforms": ["SAP"], "status": "Open", "cls": "Build role"},
        {"entity": "Acme", "platforms": ["SAP", "Veeva"], "status": "Closed", "cls": "User role"},
        {"entity": "Other", "platforms": ["SAP"], "status": "Open", "cls": "Build role"},
    ]
    counts = rules.jobs_counts(rows, ["SAP", "Veeva"], {"Acme"})
    assert counts["SAP"] == {"total": 2, "open": 1, "build": 1, "admin": 0, "tool": 0, "user": 1}
    assert counts["Veeva"]["total"] == 1


@pytest.mark.parametrize(
    ("title", "number"),
    [
        ("Senior Vice President, Finance", 1),
        ("Chief Data Officer", 1),
        ("VP IT", 2),
        ("Sr. Director, Data", 3),
        ("Associate Director, CRM", 5),
        ("Director of Sales", 4),
        ("Head of Analytics", 6),
        ("Senior Program Manager", 7),
        ("Manager, Payroll", 8),
        ("Salesforce Developer", 9),
    ],
)
def test_level(title: str, number: int) -> None:
    assert rules.level(title) == (number, rules.LEVELS[number - 1])
    assert rules.is_senior(number) is (number <= 7)


@pytest.mark.parametrize(
    ("title", "lane"),
    [
        ("Director, Commercial Excellence", rules.PEOPLE_LANES[0]),
        ("Integration Architect", rules.PEOPLE_LANES[1]),
        ("Head of Global Business Services", rules.PEOPLE_LANES[2]),
        ("Senior Manager, Veeva Vault", rules.PEOPLE_LANES[4]),
        ("SAP Basis Lead", rules.PEOPLE_LANES[3]),
        ("Director, Data Science", rules.PEOPLE_LANES[5]),
        ("VP, Digital", rules.PEOPLE_LANES[6]),
        ("President, Widgets", rules.PEOPLE_LANES[7]),
    ],
)
def test_lane(title: str, lane: str) -> None:
    assert rules.lane(title) == lane


@pytest.mark.parametrize(
    ("title", "prio"),
    [
        ("Territory Sales Representative", "C"),
        ("Key Account Manager, Salesforce", "A"),
        ("Director, Mergers and Acquisitions", "B"),
        ("Director of Global CRM Technology", "A"),
        ("President, Widgets", "B"),
    ],
)
def test_priority(title: str, prio: str) -> None:
    assert rules.priority(title) == prio


def test_named_and_always_c() -> None:
    assert rules.priority_named("Chief Information Officer") == "A"
    assert rules.priority_named("President") == "B"
    assert rules.always_c("Executive Assistant to the CEO")
    assert not rules.always_c("Director")


def test_security_and_platform_keywords() -> None:
    assert rules.is_security("Director, IT Compliance")
    assert rules.is_security("Developer", tagged=True)
    assert not rules.is_security("Director, Quality Systems")
    assert rules.names_platform("Power BI developer")
    assert not rules.names_platform("Plant manager")


def test_si_employers() -> None:
    profile: dict[str, Any] = {
        "experiences": [
            {"company": "Deloitte Consulting", "startDate": "2010-01", "endDate": "2014-06"},
            {"company": "Accenture", "startDate": "2015-01"},
            {"company": "Deloitte", "startDate": "2016-01", "endDate": "2017-01"},
            {"company": "Somewhere Else"},
        ]
    }
    assert rules.si_employers(profile) == "Deloitte (2010 to 2014); Accenture (2015 to date)"
    assert rules.si_employers({"experiences": [{"company": "Ernst & Young"}]}) == "EY"


def test_rank_rows_rule_and_seen_once(platforms: Platforms) -> None:
    jobs = [
        {
            "t": "a",
            "d": "SAP and Power BI",
            "f": "2026-01-01",
            "l": "2026-01-02",
            "status": "open",
            "url": "u1",
        },
        {"t": "b", "d": "SAP", "f": "2026-01-03", "l": None, "status": "closed", "url": "u2"},
        {"t": "c", "d": "SAP", "f": "2026-01-04", "l": "2026-01-05", "status": "open", "url": "u3"},
        {
            "t": "d",
            "d": "Power BI, HubSpot",
            "f": "2026-01-01",
            "l": "2026-01-01",
            "status": "open",
            "url": "u4",
        },
        {
            "t": "e",
            "d": "salesforce means sales",
            "f": "2026-01-01",
            "l": "2026-01-01",
            "status": "open",
            "url": "u5",
        },
    ]
    out = rules.rank_rows(
        jobs, platforms, drops={("Salesforce", "u5")}, checked=["SAP"], stack_names=["SAP S/4HANA"]
    )
    assert out.base == 4
    assert [
        (r.platform, r.count, r.open, r.last_seen, r.in_stack, r.checked) for r in out.rows
    ] == [
        ("SAP", 3, 2, "2026-01-05", "Yes", "Yes"),
        ("Power BI", 2, 2, "2026-01-02", "No", "No"),
    ]
    assert [s.platform for s in out.seen_once] == ["HubSpot"]
    assert rules.rank_rows(jobs, platforms).rows[0].in_stack == "Not pulled"


def test_rank_letter() -> None:
    assert rules.rank_letter(15, 100) == "A"
    assert rules.rank_letter(5, 100) == "B"
    assert rules.rank_letter(2, 100) == "C"
    assert rules.rank_letter(1, 100) == ""
    assert rules.rank_letter(4, 10) == "B"
    assert rules.rank_letter(5, 0) == "C"


def test_tracker_sort_and_tally() -> None:
    rows = [
        ["a", "Done", "Optional", "Nothing", "", ""],
        ["b", "Partial", "High", "A folder", "", ""],
        ["c", "Done", "High", "Nothing", "", ""],
        ["d", "Not done", "Medium", "Nothing", "", ""],
    ]
    assert [r[0] for r in rules.sort_tracker_p2(rows)] == ["c", "b", "d", "a"]
    assert rules.tracker_tally([["x", "Blocked", "", "", "", ""]], rows) == {
        "Blocked": 1,
        "Done": 2,
        "Partial": 1,
        "Not done": 1,
    }


def test_people_sort_key() -> None:
    rows = [
        {"level": 4, "priority": "B", "lane": "x", "name": "b"},
        {"level": 4, "priority": "A", "lane": "x", "name": "c"},
        {"level": 1, "priority": "C", "lane": "x", "name": "a"},
    ]
    assert [r["name"] for r in sorted(rows, key=rules.people_sort_key)] == ["a", "c", "b"]


def test_lane_pattern_lookup(platforms: Platforms) -> None:
    assert isinstance(rules.lane_pattern(platforms, "Veeva"), LanePattern)
    with pytest.raises(KeyError):
        rules.lane_pattern(platforms, "Nope")


# ---------------------------------------------------------------- the reference numbers
@reference
def test_reference_numbers_recomputed(platforms: Platforms) -> None:
    build = REFERENCE_WS / "build"
    with open(build / "prose_extra.json", encoding="utf-8") as fh:
        meta = json.load(fh)["meta"]
    entities = [
        rules.EntityRule(
            e["label"], e["counted_as_account"], tuple(e.get("patterns", ())), e.get("before")
        )
        for e in meta["entities"]
    ]
    counted = {e.label for e in entities if e.counted_as_account}
    with open(build / "jobs_all.json", encoding="utf-8") as fh:
        all_jobs = json.load(fh)
    with open(build / "drop_salesforce.json", encoding="utf-8") as fh:
        drops = {(p, u) for p, u in json.load(fh)}
    rows = []
    for j in all_jobs:
        assert rules.tag_entity(j["t"], j["d"], j["f"], entities) == j["ent"]
        skip = {p for p, u in drops if u == j["url"]}
        found = rules.match_platforms(j["t"], j["d"], platforms, skip)
        if found:
            rows.append(
                {
                    "entity": j["ent"],
                    "platforms": list(found),
                    "status": "Open" if j["status"] != "closed" else "Closed",
                }
            )
    assert len(rows) == 1897
    assert sum(1 for r in rows if r["entity"] in counted) == 1659
    counts = rules.jobs_counts(rows, list(platforms.lane_names), counted)
    assert (counts["Salesforce"]["total"], counts["Salesforce"]["open"]) == (551, 92)
    assert (counts["SAP"]["total"], counts["SAP"]["open"]) == (1068, 150)

    with open(build / "jobs_bd.json", encoding="utf-8") as fh:
        account_jobs = json.load(fh)
    with open(build / "stack_names.json", encoding="utf-8") as fh:
        stack_names = json.load(fh)
    with open(build / "checked.json", encoding="utf-8") as fh:
        checked = json.load(fh)
    ranking = rules.rank_rows(account_jobs, platforms, drops, checked, stack_names)
    assert ranking.base == 1904
    letters = {r.platform: rules.rank_letter(r.count, ranking.base) for r in ranking.rows}
    assert {p for p, g in letters.items() if g == "A"} == {"SAP", "Salesforce", "Power BI"}
    assert {p for p, g in letters.items() if g == "B"} == {"Microsoft Azure"}

    with open(build / "people_merged.json", encoding="utf-8") as fh:
        people = json.load(fh)
    senior = [p for p in people if p["senior"]]
    assert (len(people), len(senior)) == (736, 424)
    assert Counter(p["prio"] for p in senior)["A"] == 80
    assert all(rules.level(p["title"])[1] == p["L"][2:] for p in people)
    assert all(rules.is_senior(int(p["L"][0])) == p["senior"] for p in people)
    lane_misses = sum(1 for p in people if rules.lane(p["title"]) != p["lane"])
    assert lane_misses <= 1  # one analyst lane correction from a profile reading
    prio_misses = sum(1 for p in people if rules.priority(p["title"]) != p["prio"])
    assert prio_misses <= 25  # analyst corrections and the named-people rule, applied as overrides
