"""Platform role tiers counted in code (``documents.tiers``, owner request 2026-10-20): stored
tiers mapped and reconciled, R1 traps, R2 example lists, R3 one class per platform, R4
non-technical titles, R5 duplicates, R6 closed and out-of-window postings, ``classified_now``.
SYNTHETIC rows of the Jobs sheet shape; the store is faked (no database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.replica.tools import AccessTools
from documents import tiers as TIERS
from documents.tables import ColumnInfo, QueryError, TableInfo

pytestmark = pytest.mark.asyncio

COLUMNS = (
    ("requisition", "Requisition", "text", False),
    ("title", "Title", "text", False),
    ("platforms_named", "Platforms named", "text", False),
    ("class", "Class", "text", False),
    ("status", "Status", "text", False),
    ("first_seen", "First seen", "date", False),
    ("location", "Location", "text", False),
    ("platform_sentences_(exact)", "Platform sentences (exact)", "text", True),
)


def _table(extra: tuple[tuple[str, str, str, bool], ...] = ()) -> TableInfo:
    return TableInfo(
        id=uuid4(),
        name="Jobs",
        table="Jobs",
        member=None,
        document="Acme_Job_Postings.xlsx",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=tuple(ColumnInfo(n, r, t, p) for n, r, t, p in (*COLUMNS, *extra)),
        business_key="requisition",
        row_count=0,
    )


def _row(
    req: str | None,
    title: str,
    platforms: str,
    klass: str | None,
    said: str = "",
    status: str = "Open",
    first_seen: str = "2026-09-01",
    location: str = "Austin, Texas",
    **more: Any,
) -> dict[str, Any]:
    return {
        "requisition": req,
        "title": title,
        "platforms_named": platforms,
        "class": klass,
        "status": status,
        "first_seen": first_seen,
        "location": location,
        "platform_sentences_(exact)": said,
        **more,
    }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The rows the fake ``repo.table_rows`` returns, in row order from row 2."""
    rows: list[dict[str, Any]] = []

    async def table_rows(s: Any, table_id: Any, where: str, params: Any, limit: int) -> list:
        assert where == "" and params == {}
        return [SimpleNamespace(row_no=i + 2, seq=i, cells=cells) for i, cells in enumerate(rows)][
            :limit
        ]

    monkeypatch.setattr(TIERS.repo, "table_rows", table_rows)
    return rows


def _counts(out: dict[str, Any], platform: str) -> dict[str, Any]:
    return next(c for c in out["counts"] if c["platform"] == platform)


async def test_count_tiers_counts_stored_tiers_and_reconciles(store: list) -> None:
    """Every stored class lands in one tier (or Not counted / Unclassified), a row naming two
    platforms counts under each with its one class (R3), and the columns sum to the total."""
    store += [
        _row("R1", "Salesforce Developer", "Salesforce", "Build role", "Build Apex in Salesforce."),
        _row(
            "R2", "Salesforce Admin", "Salesforce", "Admin and owner role", "Administer Salesforce."
        ),
        _row(
            "R3",
            "Data Engineer",
            "Salesforce, Snowflake",
            "Working tool",
            "Integrate Salesforce data into Snowflake.",
        ),
        _row(
            "R4", "Sales Ops Analyst", "Salesforce", "User role", "Run reports in Salesforce daily."
        ),
        _row("R5", "SRE", "Salesforce", "Tools list", "Tools such as Salesforce, Jira."),
        _row("R6", "Engineer", "Salesforce", "False positive", "Customers such as Salesforce.com."),
        _row("R7", "Analyst", "Salesforce", None, "Uses Salesforce."),
        _row("R8", "Engineer", "Salesforce", "Product engineering", ""),
    ]
    out = await TIERS.count(None, _table(), {"platforms": ["Salesforce", "Data and AI"]})
    sf = _counts(out, "Salesforce")
    assert (sf["Platform role"], sf["Platform adjacent"], sf["Platform user"]) == (2, 1, 1)
    assert sf["Tools list only"] == 1 and sf["Not counted (R1)"] == 2 and sf["Unclassified"] == 1
    assert (
        sf["total_rows"]
        == 8
        == sum(sf[k] for k in (*TIERS.config().tiers, "Not counted (R1)", "Unclassified"))
    )
    assert sf["build_roles"] == 2 and sf["build_roles_with_adjacent"] == 3
    assert "'class'" in sf["tier_source"]
    ai = _counts(out, "Data and AI")
    assert ai["Platform adjacent"] == 1 and ai["total_rows"] == 1
    assert [r["row"] for r in out["unclassified_rows"]] == [8]
    assert out["base"]["rows_counted"] == 8 and out["classified_now_total"] == 0


async def test_closed_and_out_of_window_postings_leave_the_counts_and_duplicates_count_once(
    store: list,
) -> None:
    """R6 before R5: closed and pre-window rows are reported, not counted; one requisition in
    two places counts once, the earliest posting kept."""
    store += [
        _row("A1", "Tableau Developer", "Tableau", "Build role", first_seen="2026-08-01"),
        _row(
            "A1",
            "Tableau Developer",
            "Tableau",
            "Build role",
            first_seen="2026-07-15",
            location="Dublin, Ireland",
        ),
        _row("A2", "Tableau Developer", "Tableau", "Build role", status="Closed"),
        _row("A3", "Tableau Developer", "Tableau", "Build role", first_seen="2025-01-01"),
    ]
    out = await TIERS.count(None, _table(), {"platforms": ["Tableau"], "since": "2026-01-01"})
    base = out["base"]
    assert base["excluded_closed"] == 1 and base["excluded_out_of_window"] == 1
    assert base["duplicates_removed"] == 1 and base["rows_counted"] == 1
    assert _counts(out, "Tableau")["Platform role"] == 1


async def test_r1_traps_and_word_boundaries_never_match_the_platform(store: list) -> None:
    """'service now', 'self-service cloud', 'ASAP' and HashiCorp Vault are not the products; a
    row stored as ServiceNow on 'service now' alone is disputed and becomes a detection error."""
    assert not TIERS.matches(TIERS.detrap("Build a self-service cloud portal")[0], "Salesforce")
    assert not TIERS.matches("Respond ASAP to tickets", "SAP")
    assert TIERS.matches("Configure SAP S/4HANA", "SAP")
    assert not TIERS.matches(TIERS.detrap("Secrets in HashiCorp Vault")[0], "Veeva")
    assert not TIERS.matches("ai and data skills", "Data and AI")
    store.append(
        _row(
            "T1",
            "Support Engineer",
            "ServiceNow",
            "Working tool",
            "Fix issues and service now requests.",
        )
    )
    out = await TIERS.count(None, _table(), {"platforms": ["ServiceNow"]})
    (row,) = out["disputed_rows"]
    assert row["rule"] == "R1" and row["suggested_tier"] == "Not counted (R1)"
    assert _counts(out, "ServiceNow")["Platform adjacent"] == 1  # the count is never changed
    (err,) = out["detection_errors"]
    assert err["rule"] == "R1" and err["kind"] == "trap" and "service now" in err["proposed_rule"]


async def test_a_name_only_in_an_example_list_is_disputed_per_platform(store: list) -> None:
    """R2 and R3: the Data Management Engineer builds Salesforce work, but names Tableau only in
    a slash list; the one stored class is disputed for Tableau and stands for Salesforce."""
    store.append(
        _row(
            "D1",
            "Data Management Engineer",
            "Salesforce, Tableau",
            "Build role",
            "Advanced knowledge of Salesforce (managing objects). | Proven experience building"
            " Sigma Dashboards (or similar tools like Tableau/Sheets) to track quality.",
        )
    )
    out = await TIERS.count(None, _table(), {"platforms": ["Salesforce", "Tableau"]})
    (row,) = out["disputed_rows"]
    assert (row["platform"], row["rule"], row["suggested_tier"]) == (
        "Tableau",
        "R2",
        "Tools list only",
    )
    assert "Tableau/Sheets" in row["phrase"]
    assert _counts(out, "Tableau")["Platform role"] == 1


async def test_e_g_lists_and_non_technical_titles_are_disputed(store: list) -> None:
    """R2 ('e.g.' never ends the sentence) and R4 (an Account Manager stored as a platform role
    with no platform work in any responsibility)."""
    assert TIERS.sentences("Use CRM tools (e.g. Salesforce, HubSpot). Own the plan.") == [
        "Use CRM tools (e.g. Salesforce, HubSpot).",
        "Own the plan.",
    ]
    store += [
        _row(
            "E1", "Revenue Analyst", "Salesforce", "User role", "Use CRM tools (e.g. Salesforce)."
        ),
        _row(
            "E2",
            "Account Manager",
            "Salesforce",
            "Build role",
            "Sell Salesforce services to clients.",
        ),
        _row(
            "E3",
            "Platform Engineer",
            "Salesforce",
            "Tools list",
            "Develop and configure Salesforce flows.",
        ),
    ]
    out = await TIERS.count(None, _table(), {"platforms": ["Salesforce"]})
    found = {r["job_id"]: (r["rule"], r["suggested_tier"]) for r in out["disputed_rows"]}
    assert found == {
        "E1": ("R2", "Tools list only"),
        "E2": ("R4", "Platform user"),
        "E3": ("R2", "Platform role"),
    }


async def test_classified_now_fills_only_unclassified_rows(store: list) -> None:
    """The model's one-time tier lands on an Unclassified row and is counted as classified now;
    a stored tier is never overridden."""
    store += [
        _row("U1", "Veeva Admin", "Veeva", None, "Administer Veeva Vault."),
        _row("U2", "Veeva Developer", "Veeva", "Build role", "Build Veeva Vault apps."),
    ]
    out = await TIERS.count(
        None,
        _table(),
        {
            "platforms": ["Veeva"],
            "classified_now": [
                {"row": 2, "platform": "Veeva", "tier": "Platform role"},
                {"row": 3, "platform": "Veeva", "tier": "Tools list only"},
            ],
        },
    )
    veeva = _counts(out, "Veeva")
    assert veeva["Platform role"] == 2 and veeva["Unclassified"] == 0
    assert veeva["classified_now"] == 1 and out["classified_now_total"] == 1
    assert out["refused_classified_now"] == [
        {"row": 3, "platform": "Veeva", "stored_tier": "Platform role"}
    ]


async def test_a_stored_per_platform_tier_column_wins_over_the_class(store: list) -> None:
    """A '<Platform> role tier' column is the source of truth for its platform."""
    store.append(
        _row("P1", "SAP Analyst", "SAP", "Build role", "Use SAP.", sap_role_tier="Platform user")
    )
    extra = (("sap_role_tier", "SAP role tier", "text", False),)
    out = await TIERS.count(None, _table(extra), {"platforms": ["SAP"]})
    sap = _counts(out, "SAP")
    assert sap["Platform user"] == 1 and sap["Platform role"] == 0
    assert "sap_role_tier" in sap["tier_source"]


async def test_a_name_the_platforms_column_missed_is_a_detection_error(store: list) -> None:
    """R1 miss: the evidence names Databricks, the platforms column does not."""
    store.append(
        _row("M1", "Data Engineer", "AWS", "Working tool", "Build pipelines in Databricks.")
    )
    out = await TIERS.count(None, _table(), {"platforms": ["Data and AI"]})
    assert _counts(out, "Data and AI")["total_rows"] == 0
    (err,) = out["detection_errors"]
    assert (err["rule"], err["kind"], err["rows"]) == ("R1", "missed_name", [2])
    assert "'databricks'" in err["proposed_rule"]


async def test_bad_input_is_a_structured_error(store: list) -> None:
    with pytest.raises(QueryError) as bad:
        await TIERS.count(None, _table(), {"platforms": ["Workday"]})
    assert bad.value.code == "unknown_platform"
    with pytest.raises(QueryError) as bad:
        await TIERS.count(None, _table(), {"since": "last spring"})
    assert bad.value.code == "bad_since"


async def test_ask_offers_count_tiers_on_table_routes_after_describe(store: list) -> None:
    """The harness offers the tool on tabular and mixed routes only, and refuses it on a table
    the turn has not described."""
    tools = AccessTools(lambda: None, [_table()], [uuid4()])  # type: ignore[arg-type, return-value]
    assert "count_tiers" in {t["name"] for t in tools.offered("tabular")}
    assert "count_tiers" in {t["name"] for t in tools.offered("mixed")}
    assert "count_tiers" not in {t["name"] for t in tools.offered("prose")}
    out = await tools.run("count_tiers", {"table": "Jobs"})
    assert out["error"]["code"] == "describe_first"
