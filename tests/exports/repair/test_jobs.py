"""Job postings repair: merge, title repair, platforms and lanes from the dictionary only, status,
window flag, entity exclusion, placeholders. Two invented accounts; no real data."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from exports.repair.core import (
    JOBS_ALL_COLUMNS,
    JOBS_COLUMNS,
    NOT_ACCOUNT,
    NOT_FOUND,
    EntityRule,
    WorkspaceProfile,
    is_placeholder,
)
from exports.repair.jobs import (
    MAX_DESCRIPTION,
    build_jobs,
    cut_at_word,
    resolve_jobs,
    title_from_url,
)
from exports.repair.records import JobRecord

RUN = date(2026, 10, 19)


def zephyr(**kw) -> WorkspaceProfile:
    base = {
        "account_name": "Zephyr Dynamics Corporation",
        "account_short": "Zephyr",
        "domain": "zephyrdyn.com",
        "run_date": RUN,
        "window_from": date(2025, 10, 19),
        "window_to": date(2026, 10, 19),
        "domains": frozenset({"zephyrdyn.com"}),
        "aliases": ("ZD", "Zephyr Dynamics"),
        "account_label": "Zephyr",
        "entities": (
            EntityRule("Quillon Instruments", ("Quillon",), False),
            EntityRule("Zephyr (pre-close)", (), False, before="2025-01-01"),
        ),
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
        "entities": (EntityRule("Harbor Bakeries", ("Harbor Bakeries",), False),),
    }
    return WorkspaceProfile(**{**base, **kw})


def rec(n: int, title="Senior Developer", host="careers.zephyrdyn.com", **kw) -> JobRecord:
    base = {
        "key": f"k{n}",
        "url": f"https://{host}/job/{title.lower().replace(' ', '-')}/{n}",
        "title": title,
        "description": "We need a lead. Experience with Salesforce Sales Cloud is required.",
        "first_seen": date(2026, 1, 10),
        "last_seen": date(2026, 9, 1),
        "status": "open",
        "location": "Remote",
        "collected": date(2026, 10, 18),
    }
    return JobRecord(**{**base, **kw})


def only(table, name: str):
    return next(s for s in table.sheets if s.name == name)


class TestSheetsAndCells:
    def test_two_sheets_with_schema_columns(self):
        t = build_jobs(zephyr(), [rec(11111)])
        assert [s.name for s in t.sheets] == ["Job postings", "All postings (unfiltered)"]
        assert t.sheets[0].columns == JOBS_COLUMNS
        assert t.sheets[1].columns == JOBS_ALL_COLUMNS
        assert (t.key, t.title) == ("jobs", "Job postings")

    def test_platform_postings_only_on_main_sheet(self):
        t = build_jobs(
            zephyr(),
            [
                rec(11111),
                rec(22222, title="Nurse", description="Care for patients. Data analytics AI."),
            ],
        )
        assert len(t.sheets[0].rows) == 1
        assert len(t.sheets[1].rows) == 2
        assert t.stats["unique_postings"] == 2
        assert t.stats["platform_postings"] == 1

    def test_every_cell_filled_or_placeholder(self):
        bare = JobRecord(
            key="b", url="https://careers.zephyrdyn.com/job/analyst/33333", title="Analyst"
        )
        t = build_jobs(zephyr(), [rec(11111), bare])
        for sheet in t.sheets:
            for row in sheet.rows:
                assert set(row) == set(sheet.columns)
                for col, val in row.items():
                    assert val.strip(), (col, row)
        row = only(t, "All postings (unfiltered)").rows[0]
        assert all(
            is_placeholder(row[c])
            for c in ("Requisition ID", "Location", "Seniority", "Platforms named")
            if row["Title"] == "Analyst"
        )

    def test_bare_posting_cells(self):
        bare = JobRecord(
            key="b", url="https://careers.zephyrdyn.com/job/analyst/33333", title="Analyst"
        )
        row = build_jobs(zephyr(), [bare]).sheets[1].rows[0]
        assert row["Requisition ID"] == NOT_FOUND
        assert row["First seen"] == "Unknown"
        assert row["Last seen"] == "Unknown"
        assert row["Collected"].startswith("Not collected: ")
        assert row["Platforms named"] == "Not applicable"
        assert row["Status"] == "Closed (not found on site, 2026-10-19)"

    def test_dates_are_iso(self):
        row = build_jobs(zephyr(), [rec(11111, posted_at=date(2026, 2, 3))]).sheets[0].rows[0]
        assert (row["First seen"], row["Last seen"], row["Posted"]) == (
            "2026-01-10",
            "2026-09-01",
            "2026-02-03",
        )
        assert row["Collected"] == "2026-10-18"

    def test_posted_unknown_is_not_the_collection_date(self):
        row = build_jobs(zephyr(), [rec(11111)]).sheets[0].rows[0]
        assert row["Posted"] == "Unknown"
        assert row["Posted"] != row["Collected"]

    def test_platform_cells(self):
        row = build_jobs(zephyr(), [rec(11111)]).sheets[0].rows[0]
        assert row["Platforms named"] == "Salesforce"
        assert row["Lane(s)"] == "Salesforce"
        assert "Salesforce:" in row["Platform sentences"]
        assert row["Entity"] == "Zephyr"
        assert row["Class"] == "Working tool"

    def test_description_cut_at_word_boundary(self):
        long = "alpha " * 7000
        row = build_jobs(zephyr(), [rec(11111, description="Salesforce " + long)]).sheets[0].rows[0]
        cell = row["Full description"]
        assert len(cell) <= MAX_DESCRIPTION
        assert cell.endswith("alpha")

    def test_cut_at_word_short_text_untouched(self):
        assert cut_at_word("short text") == "short text"
        assert cut_at_word("aaaa bbbb cccc", 9) == "aaaa bbbb"
        assert cut_at_word("x" * 50, 10) == "x" * 10

    def test_missing_description_is_marked(self):
        row = build_jobs(zephyr(), [rec(11111, description="", title="Salesforce Admin")]).sheets[0]
        assert row.rows[0]["Full description"].startswith("Not collected: ")
        assert row.rows[0]["Platform sentences"] == "Salesforce: (named in the title only)"


class TestMerge:
    def test_same_id_two_urls_merge(self):
        a = rec(55555, first_seen=date(2026, 3, 1), last_seen=date(2026, 4, 1), description="")
        b = rec(
            55555,
            host="zephyrdyn.com",
            first_seen=date(2026, 1, 5),
            last_seen=date(2026, 9, 9),
            description="Build Salesforce flows. Use MuleSoft.",
            collected=date(2026, 10, 20),
        )
        t = build_jobs(zephyr(), [a, b])
        (p,) = t.stats["resolved"]
        assert p.first_seen == date(2026, 1, 5)
        assert p.last_seen == date(2026, 9, 9)
        assert p.description.startswith("Build Salesforce")
        assert p.platforms == ("Salesforce", "MuleSoft")
        assert len(p.urls) == 2
        assert p.collected == date(2026, 10, 20)
        assert t.stats["duplicates_removed"] == 1
        assert t.stats["rows_in"] == 2

    def test_requisition_joins_url_without_id(self):
        a = rec(
            1, url="https://zephyrdyn.com/careers/apply?x=1", requisition_id="R-9001", title="Dev"
        )
        b = rec(2, url="https://careers.zephyrdyn.com/job/dev/9001-R-9001", requisition_id="R-9001")
        assert len(resolve_jobs(zephyr(), [a, b])) == 1

    def test_other_url_id_used_when_url_has_none(self):
        a = JobRecord(
            key="a", url="https://zephyrdyn.com/apply", title="Dev",
            other_urls=("https://careers.zephyrdyn.com/job/dev/77777",),
        )  # fmt: skip
        b = rec(77777, title="Dev")
        assert len(resolve_jobs(zephyr(), [a, b])) == 1

    def test_different_ids_stay_apart(self):
        assert len(resolve_jobs(zephyr(), [rec(11111), rec(22222)])) == 2

    def test_live_board_row_kept(self):
        (p,) = resolve_jobs(zephyr(), [rec(11111, source_kind="live_board")])
        assert p.source_kind == "live_board"

    def test_live_board_flag_survives_merge(self):
        a = rec(11111, source_kind="live_board", description="")
        b = rec(11111, source_kind="feed", host="zephyrdyn.com")
        (p,) = resolve_jobs(zephyr(), [a, b])
        assert p.source_kind == "live_board"

    def test_no_source_url_dropped_and_logged(self):
        t = build_jobs(zephyr(), [JobRecord(key="x", url="", title="Dev"), rec(11111)])
        assert t.stats["unique_postings"] == 1
        assert [e.reason for e in t.exclusions if e.key == "x"] == ["no source URL"]

    def test_other_url_rescues_missing_url(self):
        r = JobRecord(
            key="x", url="", title="Dev", other_urls=("https://zephyrdyn.com/job/dev/12345",)
        )
        assert len(resolve_jobs(zephyr(), [r])) == 1

    def test_stored_platform_unions(self):
        r = rec(
            11111, description="plain text", stored_platforms=("salesforce", "Mulesoft", "Nonsense")
        )
        (p,) = resolve_jobs(zephyr(), [r])
        assert p.platforms == ("Salesforce", "MuleSoft")


class TestTitleRepair:
    def test_platform_title_recovered_from_slug(self):
        r = rec(
            12345, title="Salesforce",
            url="https://careers.zephyrdyn.com/job/senior-integration-lead/12345",
        )  # fmt: skip
        (p,) = resolve_jobs(zephyr(), [r])
        assert p.title == "Senior Integration Lead"

    def test_platform_title_other_record_title_wins(self):
        fact = rec(12345, title="SAP", url="https://zephyrdyn.com/x/12345")
        good = rec(12345, title="Basis Engineer")
        (p,) = resolve_jobs(zephyr(), [fact, good])
        assert p.title == "Basis Engineer"

    def test_platform_title_not_recoverable_dropped(self):
        r = rec(12345, title="Salesforce", url="https://careers.zephyrdyn.com/12345")
        t = build_jobs(zephyr(), [r])
        assert t.stats["unique_postings"] == 0
        (e,) = t.exclusions
        assert (e.table, e.key, e.reason) == ("jobs", "12345", "title is a platform name")

    def test_folded_platform_name_is_a_platform(self):
        for title in ("  SALESFORCE ", "Power BI", "microsoft azure"):
            r = rec(12345, title=title, url="https://zephyrdyn.com/12345")
            assert resolve_jobs(zephyr(), [r]) == []

    def test_pattern_title_is_a_platform(self):
        r = rec(12345, title="Service Cloud", url="https://zephyrdyn.com/12345")
        assert resolve_jobs(zephyr(), [r]) == []

    def test_real_title_naming_platform_kept(self):
        r = rec(12345, title="Salesforce Administrator")
        assert resolve_jobs(zephyr(), [r])[0].title == "Salesforce Administrator"

    def test_empty_title_recovered_or_dropped(self):
        ok = rec(12345, title="", url="https://zephyrdyn.com/job/data-engineer/12345")
        assert resolve_jobs(zephyr(), [ok])[0].title == "Data Engineer"
        bad = build_jobs(zephyr(), [rec(12346, title="", url="https://zephyrdyn.com/12346")])
        assert bad.stats["unique_postings"] == 0
        assert bad.exclusions[0].reason.startswith("title is empty")

    def test_slug_recovered_title_that_is_a_platform_is_dropped(self):
        r = rec(12345, title="SAP", url="https://zephyrdyn.com/job/sap/12345")
        assert resolve_jobs(zephyr(), [r]) == []

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://x.com/job/New-York/Senior-Data-Analyst_R-12345", "Senior Data Analyst"),
            ("https://x.com/job/Senior-Data-Analyst_R-12345-1", "Senior Data Analyst"),
            ("https://x.com/careers/jobs/senior-qa-lead/159/456789", "Senior Qa Lead"),
            ("https://x.com/jobs/123456/product-owner", "Product Owner"),
            ("https://x.com/jobs/product%20owner/123456", "Product Owner"),
            ("https://x.com/en/jobs/123456", ""),
            ("https://x.com/", ""),
            ("https://x.com/job/abc", ""),
            ("", ""),
        ],
    )
    def test_title_from_url(self, url, expected):
        assert title_from_url(url) == expected


class TestPlatformsAndLanes:
    def test_generic_words_are_no_platform(self):
        r = rec(11111, title="Data Scientist", description="data analytics and AI, cloud insights")
        (p,) = resolve_jobs(zephyr(), [r])
        assert p.platforms == ()
        assert p.lanes == ()
        assert p.posting_class == "Not applicable"

    def test_platforms_are_separate(self):
        r = rec(11111, title="Integration Lead", description="MuleSoft Anypoint and Power BI.")
        (p,) = resolve_jobs(zephyr(), [r])
        assert p.platforms == ("MuleSoft",)
        assert p.lanes == ("Salesforce",)

    def test_sap_is_case_sensitive(self):
        low = rec(11111, title="Clerk", description="do a sap of the tree")
        up = rec(22222, title="Analyst", description="Run SAP S/4HANA reports.")
        a, b = (resolve_jobs(zephyr(), [x])[0] for x in (low, up))
        assert a.platforms == ()
        assert b.platforms == ("SAP",)
        assert b.lanes == ("SAP",)

    def test_lane_of_platform_without_ranking_lane_is_itself(self):
        (p,) = resolve_jobs(zephyr(), [rec(11111, description="Workday HCM project.")])
        assert p.platforms == ("Workday",)
        assert p.lanes == ("Workday",)

    def test_out_of_scope_platform_never_listed(self):
        r = rec(11111, host="nfoods.org", title="BI Lead", description="Tableau and Salesforce.")
        (p,) = resolve_jobs(northgate(), [r])
        assert p.platforms == ("Salesforce",)
        assert p.lanes == ("Salesforce",)
        assert "Tableau:" not in p.sentences

    def test_out_of_scope_stored_platform_dropped(self):
        r = rec(11111, host="nfoods.org", description="nothing", stored_platforms=("Tableau",))
        (p,) = resolve_jobs(northgate(), [r])
        assert p.platforms == ()

    def test_out_of_scope_not_in_per_platform(self):
        t = build_jobs(northgate(), [])
        assert "Tableau" not in t.stats["per_platform"]
        assert "Tableau" in build_jobs(zephyr(), []).stats["per_platform"]

    def test_class_from_stored_class(self):
        for stored, label in (
            ("build", "Build role"),
            ("admin_owner", "Admin and owner role"),
            ("working_tool", "Working tool"),
            ("user", "User role"),
        ):
            r = rec(11111, stored_class=stored, description="Salesforce.")
            assert resolve_jobs(zephyr(), [r])[0].posting_class == label

    def test_class_default_user_role(self):
        r = rec(11111, description="Our company, Salesforce, is a partner.")
        assert resolve_jobs(zephyr(), [r])[0].posting_class == "User role"

    def test_unknown_stored_class_ignored(self):
        r = rec(11111, stored_class="weird", description="Salesforce.")
        assert resolve_jobs(zephyr(), [r])[0].posting_class == "User role"


class TestStatus:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("open", "Open"),
            ("OPEN", "Open"),
            ("closed", "Closed"),
            ("expired", "Closed"),
            ("filled", "Closed"),
            ("unknown", "Closed (not found on site, 2026-10-19)"),
            ("found", "Closed (not found on site, 2026-10-19)"),
            ("", "Closed (not found on site, 2026-10-19)"),
        ],
    )
    def test_words(self, word, expected):
        (p,) = resolve_jobs(zephyr(), [rec(11111, status=word)])
        assert p.status == expected
        assert is_placeholder(p.status) or p.status in ("Open", "Closed")

    def test_live_check_wins_in_merge(self):
        old = rec(11111, status="open", last_seen=date(2026, 5, 1))
        live = rec(11111, status="closed", host="zephyrdyn.com", status_checked=date(2026, 10, 18))
        assert resolve_jobs(zephyr(), [old, live])[0].status == "Closed"

    def test_status_counts(self):
        t = build_jobs(zephyr(), [rec(11111), rec(22222, status="closed"), rec(33333, status="")])
        assert t.stats["status_resolved"] == {
            "Open": 1,
            "Closed": 1,
            "Closed (not found on site)": 1,
        }

    def test_run_date_not_clock(self):
        (p,) = resolve_jobs(northgate(), [rec(11111, host="nfoods.org", status="")])
        assert p.status == "Closed (not found on site, 2026-03-01)"


class TestWindowAndEntity:
    def test_outside_window_flagged_and_kept(self):
        old = rec(11111, first_seen=date(2024, 1, 2), last_seen=date(2024, 2, 1))
        t = build_jobs(zephyr(), [old, rec(22222)])
        main = {r["Posting ID"]: r for r in t.sheets[0].rows}
        assert main["11111"]["Posted"] == "Outside window"
        assert main["11111"]["First seen"] == "2024-01-02"
        assert main["22222"]["Posted"] == "Unknown"
        assert t.stats["outside_window"] == 1
        assert t.stats["account_platform_postings"] == 1
        assert t.stats["per_platform"]["Salesforce"]["postings"] == 1
        assert any("outside the window" in e.reason for e in t.exclusions)

    def test_posted_date_decides_when_known(self):
        r = rec(11111, first_seen=date(2024, 1, 2), posted_at=date(2026, 2, 2))
        (p,) = resolve_jobs(zephyr(), [r])
        assert not p.outside_window
        r2 = rec(11111, first_seen=date(2026, 2, 2), posted_at=date(2024, 1, 2))
        assert resolve_jobs(zephyr(), [r2])[0].outside_window

    def test_unknown_date_never_flagged(self):
        r = rec(11111, first_seen=None, last_seen=None)
        assert not resolve_jobs(zephyr(), [r])[0].outside_window

    def test_window_edges_inclusive(self):
        for day in (date(2025, 10, 19), date(2026, 10, 19)):
            assert not resolve_jobs(zephyr(), [rec(11111, first_seen=day)])[0].outside_window
        assert resolve_jobs(zephyr(), [rec(11111, first_seen=date(2025, 10, 18))])[0].outside_window

    def test_divested_entity_listed_but_not_counted(self):
        div = rec(33333, description="Quillon assay lab. Salesforce users.")
        mine = rec(44444)
        t = build_jobs(zephyr(), [div, mine])
        ents = {r["Posting ID"]: r["Entity"] for r in t.sheets[0].rows}
        assert ents == {"33333": "Quillon Instruments", "44444": "Zephyr"}
        assert t.stats["per_platform"]["Salesforce"]["postings"] == 1
        assert t.stats["account_platform_postings"] == 1
        assert t.stats["non_account_entities"] == {
            "Quillon Instruments": {"postings": 1, "platform_postings": 1}
        }

    def test_pre_close_rows_not_counted(self):
        old = rec(55555, first_seen=date(2024, 6, 1), posted_at=date(2026, 1, 1))
        t = build_jobs(zephyr(), [old])
        assert t.sheets[0].rows[0]["Entity"] == "Zephyr (pre-close)"
        assert t.stats["account_platform_postings"] == 0

    def test_foreign_host_is_not_the_account(self):
        t = build_jobs(zephyr(), [rec(66666, host="jobs.stranger.org")])
        assert t.sheets[0].rows[0]["Entity"] == NOT_ACCOUNT
        assert t.stats["per_platform"]["Salesforce"]["postings"] == 0

    def test_counts_open_and_class(self):
        recs = [
            rec(10001, stored_class="build"),
            rec(10002, stored_class="build", status="closed"),
            rec(10003, stored_class="admin_owner"),
            rec(10004, stored_class="user", status="closed"),
            rec(10005, description="Use SAP daily.", title="Clerk", stored_class="working_tool"),
        ]
        t = build_jobs(zephyr(), recs)
        sf = t.stats["per_platform"]["Salesforce"]
        assert sf == {"postings": 4, "open": 2, "build": 2, "admin": 1, "tool": 0, "user": 1}
        assert t.stats["per_platform"]["SAP"]["tool"] == 1
        assert t.stats["account_platform_postings"] == 5

    def test_same_postings_other_account(self):
        recs = [rec(10001, host="nfoods.org"), rec(10002, host="careers.zephyrdyn.com")]
        t = build_jobs(northgate(), recs)
        by = {r["Posting ID"]: r["Entity"] for r in t.sheets[0].rows}
        assert by == {"10001": "Northgate Foods", "10002": NOT_ACCOUNT}


class TestNeutralityAndIdempotence:
    def test_no_account_name_in_module(self):
        import exports.repair.entity as entity
        import exports.repair.jobs as jobs
        import exports.repair.tech as tech

        for mod in (jobs, entity, tech):
            src = Path(mod.__file__).read_text(encoding="utf-8").lower()
            for word in ("becton", "waters", "athena health", "salesforce.com/"):
                assert word not in src

    def test_building_twice_is_equal(self):
        recs = [
            rec(11111),
            rec(22222, status="closed"),
            rec(33333, title="SAP", url="https://x.org/33333"),
        ]
        a, b = build_jobs(zephyr(), recs), build_jobs(zephyr(), list(reversed(recs)))
        assert [s.rows for s in a.sheets] == [s.rows for s in b.sheets]
        assert a.exclusions == b.exclusions
        assert {k: v for k, v in a.stats.items() if k != "resolved"} == {
            k: v for k, v in b.stats.items() if k != "resolved"
        }

    def test_empty_input(self):
        t = build_jobs(zephyr(), [])
        assert t.sheets[0].rows == [] and t.stats["unique_postings"] == 0

    def test_resolved_is_frozen(self):
        (p,) = resolve_jobs(zephyr(), [rec(11111)])
        with pytest.raises(AttributeError):
            p.title = "x"  # type: ignore[misc]
