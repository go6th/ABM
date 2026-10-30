"""Tech stack and teams: ranking base and ranks, formula convention, stack merge and rejection,
detection domains, teams. Two invented accounts and a custom dictionary prove neutrality."""

from __future__ import annotations

from datetime import date

import pytest

from exports.repair.core import (
    RANKING_COLUMNS,
    STACK_COLUMNS,
    TEAM_COLUMNS,
    EntityRule,
    WorkspaceProfile,
)
from exports.repair.jobs import ResolvedPosting, build_jobs, resolve_jobs
from exports.repair.records import JobRecord, TechRecord
from exports.repair.tech import FORMULA_PREFIX, build_tech, reject_reason, tech_key
from parity.platforms import platforms_from_data


def zephyr(**kw) -> WorkspaceProfile:
    base = {
        "account_name": "Zephyr Dynamics Corporation",
        "account_short": "Zephyr",
        "domain": "zephyrdyn.com",
        "run_date": date(2026, 10, 19),
        "window_from": date(2025, 10, 19),
        "window_to": date(2026, 10, 19),
        "domains": frozenset({"zephyrdyn.com"}),
        "aliases": ("ZD",),
        "account_label": "Zephyr",
        "entities": (EntityRule("Quillon Instruments", ("Quillon",), False),),
    }
    return WorkspaceProfile(**{**base, **kw})


def northgate(**kw) -> WorkspaceProfile:
    base = {
        "account_name": "Northgate Foods Inc",
        "account_short": "Northgate",
        "domain": "nfoods.org",
        "run_date": date(2026, 3, 1),
        "window_from": date(2025, 3, 1),
        "window_to": date(2026, 3, 1),
        "domains": frozenset({"nfoods.org"}),
        "aliases": ("Northgate",),
        "account_label": "Northgate Foods",
        "out_of_scope_platforms": ("Tableau",),
    }
    return WorkspaceProfile(**{**base, **kw})


def job(n: int, text: str, host="careers.zephyrdyn.com", **kw) -> JobRecord:
    base = {
        "key": f"k{n}",
        "url": f"https://{host}/job/role-{n}/{n}",
        "title": f"Role {n}",
        "description": text,
        "first_seen": date(2026, 2, 1),
        "last_seen": date(2026, 9, 1),
        "status": "open",
        "location": "Remote",
        "team": "Platform Team",
    }
    return JobRecord(**{**base, **kw})


def det(tech: str, **kw) -> TechRecord:
    base = {
        "technology": tech,
        "detections": 1,
        "first_seen": "2026-01-02T10:00:00Z",
        "last_seen": "2026-09-30",
        "source_url": "https://www.zephyrdyn.com/about",
        "detected_domain": "zephyrdyn.com",
        "category": "Cloud",
    }
    return TechRecord(**{**base, **kw})


def resolved(profile, records) -> list[ResolvedPosting]:
    return build_jobs(profile, records).stats["resolved"]


def sheet(table, name):
    return next(s for s in table.sheets if s.name == name)


def ranking_fixture():
    """20 account postings: Salesforce 10, SAP 4, Veeva 2, Workday 1, 3 without a platform."""
    recs = [job(10000 + i, "Salesforce Sales Cloud administration.") for i in range(10)]
    recs += [job(20000 + i, "Run SAP S/4HANA finance.") for i in range(4)]
    recs += [job(30000 + i, "Veeva Vault CRM rollout.") for i in range(2)]
    recs += [job(40000, "Workday payroll.")]
    recs += [job(50000 + i, "General office work.") for i in range(3)]
    return recs


class TestSheets:
    def test_three_sheets_in_order_with_schema(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        assert [s.name for s in t.sheets] == [
            "Platform Ranking",
            "Confirmed stack",
            "Platform teams",
        ]
        assert [s.columns for s in t.sheets] == [RANKING_COLUMNS, STACK_COLUMNS, TEAM_COLUMNS]
        assert (t.key, t.title) == ("tech", "Tech stack and teams")

    def test_empty_inputs(self):
        t = build_tech(zephyr(), [], [])
        assert all(s.rows == [] for s in t.sheets)
        assert t.stats["ranking_base"] == 0
        assert t.notes


class TestRanking:
    def test_base_counts_account_postings_naming_a_platform(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        assert t.stats["ranking_base"] == 17

    def test_ranks_and_counts(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        rows = {r["Platform"]: r for r in sheet(t, "Platform Ranking").rows}
        assert rows["Salesforce"]["Postings naming it"] == "10"
        assert rows["Salesforce"]["Rank"] == "A"  # 10/17 = 58.8%
        assert rows["SAP"]["Rank"] == "B"  # 4 postings: share >= 15% but fewer than 5
        assert rows["Veeva"]["Rank"] == "C"
        assert rows["Workday"]["Rank"] == "Seen once"
        assert rows["Salesforce"]["Share of base"] == "58.8%"
        assert rows["Salesforce"]["Open now"] == "10"
        assert rows["Veeva"]["Lane"] == "Veeva"
        assert rows["Workday"]["Lane"] == "Outside the lanes"

    def test_rows_sorted_by_postings(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        counts = [int(r["Postings naming it"]) for r in sheet(t, "Platform Ranking").rows]
        assert counts == sorted(counts, reverse=True)

    def test_formula_convention(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        rows = sheet(t, "Platform Ranking").rows
        first = rows[0]
        assert first[FORMULA_PREFIX + "Share of base"] == "=E2/17"
        assert rows[1][FORMULA_PREFIX + "Share of base"] == "=E3/17"
        rank = first[FORMULA_PREFIX + "Rank"]
        assert rank.startswith("=IF(AND(F2>=0.15,E2>=5)")
        assert "Seen once" in rank
        assert "__formula__" not in "".join(sheet(t, "Platform Ranking").columns)
        assert all(len(c) == len(RANKING_COLUMNS) for c in sheet(t, "Platform Ranking").cell_rows())

    def test_rank_value_agrees_with_formula_thresholds(self):
        # the formula text and the computed letter use the same thresholds
        t = build_tech(zephyr(), [], resolved(zephyr(), ranking_fixture()))
        text = sheet(t, "Platform Ranking").rows[0][FORMULA_PREFIX + "Rank"]
        for token in ("0.15", ">=5", "0.05", ">=3", ">=2"):
            assert token in text

    def test_out_of_window_and_non_account_excluded(self):
        recs = ranking_fixture()
        recs += [
            job(60000 + i, "Salesforce Sales Cloud.", first_seen=date(2023, 1, 1)) for i in range(5)
        ]
        recs += [job(61000 + i, "Quillon Salesforce Sales Cloud.") for i in range(5)]
        recs += [job(62000 + i, "Salesforce.", host="jobs.stranger.org") for i in range(5)]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        assert t.stats["ranking_base"] == 17
        rows = {r["Platform"]: r for r in sheet(t, "Platform Ranking").rows}
        assert rows["Salesforce"]["Postings naming it"] == "10"

    def test_ranking_uses_ranking_dictionary_beyond_lane_platforms(self):
        recs = [
            job(70000 + i, "Our data lake runs on Snowflake and Azure.", title=f"Eng {i}")
            for i in range(6)
        ]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        rows = {r["Platform"]: r for r in sheet(t, "Platform Ranking").rows}
        assert rows["Snowflake"]["Rank"] == "A"
        assert rows["Microsoft Azure"]["Category"] == "Cloud"
        assert t.stats["ranking_base"] == 6

    def test_every_lane_platform_found_appears(self):
        recs = ranking_fixture()
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        names = {r["Platform"] for r in sheet(t, "Platform Ranking").rows}
        lanes = {p for r in resolved(zephyr(), recs) if r.counted for p in r.platforms}
        assert lanes <= names

    def test_lane_platform_missing_from_ranking_dictionary_still_appears(self):
        plat = platforms_from_data(
            {
                "lanes": [{"name": "Kronos", "pattern": "kronos"}],
                "ranking": [{"name": "Foo", "category": "Tools", "lane": "", "pattern": "foo"}],
                "out_of_scope": [],
            }
        )
        prof = zephyr(platforms=plat)
        recs = [job(80000 + i, "kronos timekeeping and foo") for i in range(3)]
        t = build_tech(prof, [], resolved(prof, recs))
        rows = {r["Platform"]: r for r in sheet(t, "Platform Ranking").rows}
        assert set(rows) == {"Foo", "Kronos"}
        assert rows["Kronos"]["Rank"] == "B"  # 3 of 3
        assert rows["Kronos"]["Category"] == "Not found"
        assert rows["Kronos"]["Lane"] == "Kronos"

    def test_out_of_scope_platform_not_ranked(self):
        recs = [
            job(90000 + i, "Tableau and Salesforce reporting", host="nfoods.org") for i in range(6)
        ]
        prof = northgate()
        t = build_tech(prof, [], resolved(prof, recs))
        names = {r["Platform"] for r in sheet(t, "Platform Ranking").rows}
        assert "Tableau" not in names and "Salesforce" in names

    def test_single_posting_is_seen_once_even_when_base_is_one(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), [job(1234, "Salesforce.")]))
        (row,) = sheet(t, "Platform Ranking").rows
        assert row["Rank"] == "Seen once" and row["Share of base"] == "100.0%"

    def test_each_posting_counted_once_per_platform(self):
        t = build_tech(
            zephyr(), [], resolved(zephyr(), [job(1234, "Salesforce Sales Cloud Salesforce")])
        )
        assert sheet(t, "Platform Ranking").rows[0]["Postings naming it"] == "1"

    def test_in_confirmed_stack_follows_detections(self):
        recs = ranking_fixture()
        t = build_tech(zephyr(), [det("Salesforce")], resolved(zephyr(), recs))
        rows = {r["Platform"]: r for r in sheet(t, "Platform Ranking").rows}
        assert rows["Salesforce"]["In confirmed stack"] == "Yes"
        assert rows["SAP"]["In confirmed stack"] == "No"


class TestNormalisation:
    @pytest.mark.parametrize(
        "variants",
        [
            ("AWS Athena", "Amazon Athena", "Athena"),
            ("Azure ML", "Azure Machine Learning", "Microsoft Azure Machine Learning"),
            ("Power BI", "Microsoft Power BI", "PowerBI", "MS Power BI"),
            ("AWS", "Amazon Web Services", "Amazon AWS"),
            ("Azure", "Microsoft Azure", "MS Azure"),
            ("GCP", "Google Cloud Platform", "Google Cloud"),
            ("Snowflake", "Snowflake Platform", "snowflake"),
            ("Okta", "Okta Suite"),
        ],
    )
    def test_variants_collapse(self, variants):
        assert len({tech_key(v)[0] for v in variants}) == 1

    def test_different_technologies_stay_apart(self):
        assert tech_key("Power BI")[0] != tech_key("Tableau")[0]
        assert tech_key("Azure Machine Learning")[0] != tech_key("Microsoft Azure")[0]
        assert tech_key("MuleSoft")[0] != tech_key("Salesforce")[0]

    def test_display_is_canonical_for_alias(self):
        assert tech_key("aws athena")[1] == "Amazon Athena"
        assert tech_key("Okta")[1] == "Okta"


class TestRejection:
    @pytest.mark.parametrize(
        ("value", "fragment"),
        [
            ("", "empty"),
            ("x" * 41, "longer"),
            ("Runs on the cloud.", "full stop"),
            ("one two three four five six", "words"),
            ("1,700+ data scientists", "statistic"),
            ("40% faster pipelines", "statistic"),
            ("Zephyr uses Snowflake", "verb"),
            ("is a platform", "verb"),
            ("Leveraging Azure", "verb"),
        ],
    )
    def test_rejected(self, value, fragment):
        assert fragment in reject_reason(value)

    @pytest.mark.parametrize(
        "value",
        ["Snowflake", "Azure Data Factory", "Power BI", "IT Service Management", "7-Zip",
         "Zendesk Support", "10x Genomics", "Jira and Confluence", "C++", "SAP S/4HANA"],
    )  # fmt: skip
    def test_accepted(self, value):
        assert reject_reason(value) == ""

    def test_rejections_logged_not_stacked(self):
        recs = [det("1,700+ data scientists"), det("x" * 50), det("Datadog")]
        t = build_tech(zephyr(), recs, [])
        assert [r["Technology"] for r in sheet(t, "Confirmed stack").rows] == ["Datadog"]
        reasons = [e.reason for e in t.exclusions]
        assert len(reasons) == 2 and all("rejected" in r for r in reasons)
        assert all(e.table == "tech" for e in t.exclusions)


class TestStack:
    def test_cross_check_values(self):
        recs = [job(1000 + i, "Azure and Snowflake pipelines.") for i in range(3)]
        dets = [det("Azure"), det("Datadog")]
        t = build_tech(zephyr(), dets, resolved(zephyr(), recs))
        rows = {r["Technology"]: r for r in sheet(t, "Confirmed stack").rows}
        assert rows["Microsoft Azure"]["Cross check"] == "Both"
        assert rows["Snowflake"]["Cross check"] == "Postings only"
        assert rows["Datadog"]["Cross check"] == "Detection only"
        assert all(r["Cross check"] for r in sheet(t, "Confirmed stack").rows)
        assert t.stats["cross_check"] == {"Both": 1, "Postings only": 1, "Detection only": 1}

    def test_variants_merge_into_one_row_with_summed_detections(self):
        dets = [det("AWS Athena"), det("Amazon Athena", detections=3), det("Athena")]
        t = build_tech(zephyr(), dets, [])
        (row,) = sheet(t, "Confirmed stack").rows
        assert row["Technology"] == "Amazon Athena"
        assert row["Detections"] == "5"
        assert row["Postings naming it"] == "0"

    def test_azure_ml_variants_merge(self):
        dets = [det("Azure ML"), det("Azure Machine Learning")]
        t = build_tech(zephyr(), dets, [])
        assert [r["Technology"] for r in sheet(t, "Confirmed stack").rows] == [
            "Azure Machine Learning"
        ]

    def test_detections_from_foreign_domain_dropped_and_logged(self):
        dets = [
            det("Datadog", detected_domain="competitor.com", source_url="https://competitor.com/x"),
            det("Okta", detected_domain="", source_url="https://news.example.org/a"),
            det("Slack", detected_domain="careers.zephyrdyn.com"),
        ]
        t = build_tech(zephyr(), dets, [])
        assert [r["Technology"] for r in sheet(t, "Confirmed stack").rows] == ["Slack"]
        logged = {e.key: e.detail for e in t.exclusions}
        assert logged == {"Datadog": "competitor.com", "Okta": "news.example.org"}

    def test_domain_taken_from_source_url_when_blank(self):
        t = build_tech(zephyr(), [det("Okta", detected_domain="")], [])
        assert sheet(t, "Confirmed stack").rows[0]["Detected domain"] == "zephyrdyn.com"

    def test_domain_rule_follows_the_profile(self):
        rec = det("Okta", detected_domain="nfoods.org", source_url="https://nfoods.org/a")
        assert build_tech(northgate(), [rec], []).stats["stack_rows"] == 1
        assert build_tech(zephyr(), [rec], []).stats["stack_rows"] == 0

    def test_no_source_url_dropped_and_logged(self):
        t = build_tech(zephyr(), [det("Okta", source_url="")], [])
        assert t.stats["stack_rows"] == 0
        assert t.exclusions[0].reason == "no source URL"

    def test_source_type_and_domain_columns(self):
        recs = [job(1000 + i, "Snowflake.") for i in range(2)]
        t = build_tech(zephyr(), [det("Okta")], resolved(zephyr(), recs))
        rows = {r["Technology"]: r for r in sheet(t, "Confirmed stack").rows}
        assert rows["Okta"]["Source type"] == "Website detection"
        assert rows["Okta"]["Detected domain"] == "zephyrdyn.com"
        assert rows["Snowflake"]["Source type"] == "Job postings"
        assert rows["Snowflake"]["Detected domain"] == "Not applicable"
        assert rows["Snowflake"]["Source URL"].startswith("https://careers.zephyrdyn.com/job/")

    def test_dates(self):
        t = build_tech(
            zephyr(), [det("Okta"), det("Okta", first_seen="2025-12-01", last_seen="")], []
        )
        row = sheet(t, "Confirmed stack").rows[0]
        assert (row["First seen"], row["Last seen"]) == ("2025-12-01", "2026-09-30")

    def test_unknown_dates(self):
        t = build_tech(zephyr(), [det("Okta", first_seen="", last_seen="garbage")], [])
        row = sheet(t, "Confirmed stack").rows[0]
        assert (row["First seen"], row["Last seen"]) == ("Unknown", "Unknown")

    def test_postings_dates_merge(self):
        recs = [
            job(1000, "Snowflake.", first_seen=date(2026, 1, 1), last_seen=date(2026, 3, 1)),
            job(1001, "Snowflake.", first_seen=date(2026, 2, 1), last_seen=date(2026, 8, 1)),
        ]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        row = sheet(t, "Confirmed stack").rows[0]
        assert (row["First seen"], row["Last seen"]) == ("2026-01-01", "2026-08-01")
        assert row["Source URL"].endswith("/1001")  # the posting seen last

    def test_category_and_function_placeholders(self):
        t = build_tech(zephyr(), [det("Okta", category="", business_function="")], [])
        row = sheet(t, "Confirmed stack").rows[0]
        assert row["Category"] == "Not found" and row["Business function"] == "Not found"

    def test_record_category_beats_dictionary(self):
        recs = [job(1000, "Snowflake.")]
        t = build_tech(zephyr(), [det("Snowflake", category="Warehouse", business_function="Data")],
                       resolved(zephyr(), recs))  # fmt: skip
        row = sheet(t, "Confirmed stack").rows[0]
        assert (row["Category"], row["Business function"]) == ("Warehouse", "Data")

    def test_dictionary_category_for_postings_only(self):
        t = build_tech(zephyr(), [], resolved(zephyr(), [job(1000, "Snowflake.")]))
        row = sheet(t, "Confirmed stack").rows[0]
        assert (row["Category"], row["Business function"]) == ("Data platform", "Data and AI")

    def test_zero_detection_count_means_one(self):
        t = build_tech(zephyr(), [det("Okta", detections=0)], [])
        assert sheet(t, "Confirmed stack").rows[0]["Detections"] == "1"

    def test_every_cell_filled(self):
        recs = ranking_fixture()
        t = build_tech(zephyr(), [det("Okta"), det("Azure")], resolved(zephyr(), recs))
        for s in t.sheets:
            for row in s.cell_rows():
                assert all(c.strip() for c in row), (s.name, row)


class TestTeams:
    def test_grouping_and_columns(self):
        recs = [
            job(1000, "Salesforce.", team="Revenue Systems", function="IT", location="Austin"),
            job(1001, "MuleSoft.", team="Revenue Systems", function="IT", location="Austin"),
            job(1002, "SAP finance.", team="Revenue Systems", function="IT", location="Berlin"),
            job(1003, "Workday.", team="", function="People Tech", location=""),
        ]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        rows = sheet(t, "Platform teams").rows
        assert rows[0] == {
            "Team": "Revenue Systems", "Function": "IT", "Location": "Austin", "Postings": "2",
            "Platforms": "MuleSoft, Salesforce",
        }  # fmt: skip
        people = next(r for r in rows if r["Team"] == "People Tech")
        assert people["Location"] == "Not found" and people["Function"] == "People Tech"
        assert len(rows) == 3

    def test_postings_without_platform_or_team_skipped(self):
        recs = [
            job(1000, "no platform here", team="Ops"),
            job(1001, "Salesforce.", team="", function=""),
        ]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        assert sheet(t, "Platform teams").rows == []
        assert t.stats["postings_without_team"] == 1

    def test_top_25(self):
        recs = [job(2000 + i, "Salesforce.", team=f"Team {i:02d}") for i in range(30)]
        recs += [job(3000 + i, "Salesforce.", team="Team 29") for i in range(3)]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        rows = sheet(t, "Platform teams").rows
        assert len(rows) == 25
        assert rows[0]["Team"] == "Team 29" and rows[0]["Postings"] == "4"

    def test_non_account_and_out_of_window_not_in_teams(self):
        recs = [
            job(1000, "Quillon Salesforce.", team="Divested"),
            job(1001, "Salesforce.", team="Old", first_seen=date(2020, 1, 1)),
            job(1002, "Salesforce.", team="Mine"),
        ]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        assert [r["Team"] for r in sheet(t, "Platform teams").rows] == ["Mine"]


class TestNeutralityAndIdempotence:
    def test_two_accounts_same_code(self):
        a = build_tech(zephyr(), [det("Okta")], resolved(zephyr(), ranking_fixture()))
        recs_b = [job(5000 + i, "Salesforce Sales Cloud.", host="nfoods.org") for i in range(5)]
        dets_b = [det("Okta", detected_domain="nfoods.org", source_url="https://nfoods.org/")]
        b = build_tech(northgate(), dets_b, resolved(northgate(), recs_b))
        assert a.stats["ranking_base"] == 17 and b.stats["ranking_base"] == 5
        assert sheet(b, "Platform Ranking").rows[0]["Rank"] == "A"
        stack_b = {r["Technology"]: r["Cross check"] for r in sheet(b, "Confirmed stack").rows}
        assert stack_b == {"Salesforce": "Postings only", "Okta": "Detection only"}

    def test_other_account_postings_do_not_leak(self):
        recs = [job(5000 + i, "Salesforce.", host="nfoods.org") for i in range(5)]
        t = build_tech(zephyr(), [], resolved(zephyr(), recs))
        assert t.stats["ranking_base"] == 0

    def test_building_twice_is_equal(self):
        posts = resolved(zephyr(), ranking_fixture())
        dets = [det("AWS Athena"), det("Athena"), det("bad value."), det("Okta")]
        a, b = build_tech(zephyr(), dets, posts), build_tech(zephyr(), list(reversed(dets)), posts)
        assert [s.rows for s in a.sheets] == [s.rows for s in b.sheets]
        assert a.exclusions == sorted(b.exclusions, key=lambda e: a.exclusions.index(e))
        assert a.stats == b.stats

    def test_inputs_not_mutated(self):
        posts = resolve_jobs(zephyr(), ranking_fixture())
        snapshot = list(posts)
        build_tech(zephyr(), [det("Okta")], posts)
        assert posts == snapshot
