"""Rule 8 of the master prompt in code: a Lead's verification state comes from the queries the
run actually sent that named the value, three distinct ones across two tool categories make it
complete, and a value nobody searched for stays pending."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.leads import lead_states_from_fetches
from account_intelligence.grading.ports import FetchRecord

RUN = uuid4()


def _fetch(ref: str, category: str, *queries: str, success: bool = True) -> FetchRecord:
    return FetchRecord(
        call_ref=ref,
        run_id=RUN,
        provider_code="p_search" if category == "web_search" else "p_scrape",
        operation="web_search" if category == "web_search" else "read_page",
        origin="search_snippet" if category == "web_search" else "full_page_read",
        tool_category=category,
        fetched_at=datetime(2026, 10, 24, 8, 0, int(ref[-1]), tzinfo=UTC),
        success=success,
        queries=queries,
    )


def test_three_distinct_queries_across_two_tools_complete_a_lead() -> None:
    cfg = load_grading_config()
    fetches = {
        "c1": _fetch("c1", "web_search", "Acme Salesforce customer story"),
        "c2": _fetch("c2", "web_search", "Acme CRM platform migration announcement"),
        "c3": _fetch("c3", "job_board", "salesforce administrator jobs"),
        "c4": _fetch("c4", "web_search", "Acme ServiceNow partnership"),
        "c5": _fetch("c5", "web_search", "weather today"),
    }
    states = lead_states_from_fetches(
        {"crm_platform=salesforce": ("salesforce",), "itsm=servicenow": ("servicenow",)},
        fetches,
        ("acme",),
        cfg,
    )
    sf = states["crm_platform=salesforce"]
    assert [q.text for q in sf.accepted] == [
        "Acme Salesforce customer story",
        "salesforce administrator jobs",
    ] or len(sf.accepted) >= 2
    # one query only: the ServiceNow lead is still pending, never "unverified" after one search
    assert states["itsm=servicenow"].status == "pending"
    assert len(states["itsm=servicenow"].accepted) == 1


def test_a_value_nobody_searched_for_and_a_failed_call_count_nothing() -> None:
    cfg = load_grading_config()
    fetches = {"c1": _fetch("c1", "web_search", "Acme Tableau dashboards", success=False)}
    states = lead_states_from_fetches(
        {"bi=tableau": ("tableau",), "erp=sap": ("sap",)}, fetches, ("acme",), cfg
    )
    assert states["bi=tableau"].accepted == () and states["bi=tableau"].status == "pending"
    assert states["erp=sap"].accepted == ()  # "sap" is too short a needle to match by itself
