"""End to end on real infrastructure: connector calls -> ledger -> adapters -> grading ->
fact write. Fake provider server and synthetic page text; real Postgres with forced RLS."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import psycopg
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.factory import build_connectors
from account_intelligence.connectors.models import ScrapeJobPageArgs, WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from account_intelligence.grading import repo
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.pipeline import grade_claims, load_inputs
from account_intelligence.grading.ports import RunInfo
from account_intelligence.connectors.settings import load_providers_config
from tests.conftest import ADMIN_DSN
from tests.connectors.conftest import T0, Harness, Reply, context, harness, make_run  # noqa: F401
from tests.fakes.grading_fakes import ScriptedJudge

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

CAREERS = "https://careers.acme.example/jobs/42"
PAGE = ("# Senior Salesforce Administrator\n\nAcme is hiring a Senior Salesforce Administrator to own "
        "the CRM platform. The team uses Salesforce Sales Cloud.")
SEARCH = {"search_id": "s1", "session_id": "x", "results": [
    {"url": "https://blog.example.net/acme-crm", "title": "Acme CRM", "publish_date": None,
     "excerpts": ["Acme appears to run Salesforce for its CRM."]}]}


async def test_confirmed_end_to_end_and_snippet_never_confirms(harness: Harness, tenant_a: UUID) -> None:  # noqa: F811
    account, run, domain = make_run(tenant_a, domain="acme.example")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SCRAPE].script(
        Reply(body={"success": True, "data": {"markdown": PAGE, "metadata": {"sourceURL": CAREERS, "url": CAREERS, "statusCode": 200}}}))
    harness.providers[ProviderCode.P_SEARCH].script(Reply(body=SEARCH), Reply(status=500, body={}))
    rt = harness.runtime()
    await call_operation(Operation.SCRAPE_JOB_PAGE, ScrapeJobPageArgs(url=CAREERS, page_kind="job_page"), ctx, rt)
    assert harness.providers[ProviderCode.P_SCRAPE].bodies()[0]["maxAge"] == 0
    await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("acme crm vendor",)), ctx, rt)
    with pytest.raises(Exception):
        await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("acme crm platform",)), ctx, rt)

    cfg = load_grading_config()
    adapter = LedgerAdapter(tenant_txn, harness.payloads, build_connectors(load_providers_config()), cfg)
    info = RunInfo(tenant_a, account, run, T0 - timedelta(hours=1), domain, "Acme")
    inputs = await load_inputs(info, adapter, adapter)
    assert {f.success for f in inputs.fetches.values()} == {True, False}
    origins = {s.url: s.origin for s in inputs.sources}
    assert origins == {CAREERS: "full_page_read", "https://blog.example.net/acme-crm": "search_snippet"}
    search_fetch = next(f for f in inputs.fetches.values() if f.success and f.operation == "web_search")
    assert search_fetch.queries == ("acme crm vendor",) and search_fetch.tool_category == "web_search"

    proposals = [
        {"claim_kind": "sourced", "local_id": "j", "attribute_key": "jobs.salesforce_admin.open",
         "value": "Senior Salesforce Administrator",
         "evidence": [{"url": CAREERS, "supporting_sentence": "Acme is hiring a Senior Salesforce Administrator to own the CRM platform."}]},
        {"claim_kind": "sourced", "local_id": "c", "attribute_key": "tech.crm.vendor", "value": "Salesforce",
         "evidence": [{"url": "https://blog.example.net/acme-crm", "supporting_sentence": "Acme appears to run Salesforce for its CRM."}],
         "confirming_question": "Is Salesforce Acme's CRM of record?"},
    ]
    result = await grade_claims(proposals, info, inputs, cfg, T0 + timedelta(hours=1),
                                judge=ScriptedJudge([{"supported": True, "reason": "stated"}]), judge_profile_ok=True)
    grades = {g.attribute_key: g for g in result.graded}
    assert grades["jobs.salesforce_admin.open"].product_grade == "confirmed"
    crm = grades["tech.crm.vendor"]
    assert crm.product_grade == "lead" and not crm.read_in_primary_this_run and crm.lead and crm.lead.status == "pending"

    ids = [await repo.record_graded_fact(tenant_txn, info, g) for g in result.graded]
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute("SELECT product_grade, lead_status FROM facts WHERE id = ANY(%s) ORDER BY product_grade", (ids,)).fetchall()
        urls = {r[0] for r in c.execute("SELECT url FROM fact_evidence WHERE fact_id = ANY(%s)", (ids,)).fetchall()}
    assert rows == [("confirmed", None), ("lead", "pending")]
    assert urls == {CAREERS, "https://blog.example.net/acme-crm"}
