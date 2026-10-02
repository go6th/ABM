"""The tracker's Lead check delegates to the grading layer (one implementation)."""

from account_intelligence.tracker.leads import is_differently_worded, verify_lead_proof
from account_intelligence.tracker.model import CallRecord


def call(ref: str, cat: str, *queries: str, ok: bool = True) -> CallRecord:
    return CallRecord(call_ref=ref, tool_category=cat, success=ok, queries=queries)


def test_three_distinct_queries_across_two_categories_prove_the_lead() -> None:
    calls = [call("a", "web_search", "salesforce administrator job"), call("b", "job_board", "crm platform team opening"),
             call("c", "web_search", "service cloud migration partner")]
    assert verify_lead_proof(calls)


def test_reworded_duplicates_failed_calls_and_one_category_do_not() -> None:
    assert not verify_lead_proof([call("a", "web_search", "salesforce admin hiring"),
                                  call("b", "job_board", "Hiring, Salesforce ADMIN!"),
                                  call("c", "web_search", "admin hiring salesforce")])
    assert not verify_lead_proof([call("a", "web_search", "one alpha"), call("b", "job_board", "two beta", ok=False),
                                  call("c", "web_search", "three gamma")])
    assert not verify_lead_proof([call("a", "web_search", "one alpha", "two beta", "three gamma")])
    assert not is_differently_worded("Acme CRM migration", "migration crm acme")
    assert is_differently_worded("acme crm migration", "salesforce admin hiring")
