"""The verification stage on real Postgres: a Confirmed fact built through the connector and the
grading engine is re-read blind; an honest re-read keeps it; a fabricated quote, a wrong value or
a verifier of the generator's family supersede it with the capped claim (a new row and a grade
event by ``verifier``); every result is a finding; a second pass writes nothing new (V8)."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.factory import build_connectors
from account_intelligence.connectors.models import ScrapeJobPageArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn
from account_intelligence.grading import repo
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.pipeline import grade_claims, load_inputs
from account_intelligence.grading.ports import RunInfo
from account_intelligence.platform.ports import LlmResult, LlmUsage
from tests.conftest import ADMIN_DSN
from tests.connectors.conftest import T0, Harness, Reply, context, make_run
from tests.connectors.conftest import harness as harness  # pytest fixture re-export
from tests.fakes.grading_fakes import ScriptedJudge
from verify.config import load_verify_config
from verify.stage import verify_confirmed

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
CAREERS = "https://careers.acme.example/jobs/42"
SENTENCE = "Acme is hiring a Senior Salesforce Administrator to own the CRM platform."
PAGE = f"# Senior Salesforce Administrator\n\n{SENTENCE} The team uses Salesforce Sales Cloud."


def admin(sql: str, *p: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, p)
        return cur.fetchall() if cur.description else []


async def confirmed_fact(harness: Harness, tenant: UUID) -> tuple[Any, ...]:
    account, run, domain = make_run(tenant, domain="acme.example")
    harness.providers[ProviderCode.P_SCRAPE].script(
        Reply(
            body={
                "success": True,
                "data": {
                    "markdown": PAGE,
                    "metadata": {"sourceURL": CAREERS, "url": CAREERS, "statusCode": 200},
                },
            }
        )
    )
    await call_operation(
        Operation.SCRAPE_JOB_PAGE,
        ScrapeJobPageArgs(url=CAREERS, page_kind="job_page"),
        context(tenant, account, run, domain),
        harness.runtime(),
    )
    cfg = load_grading_config()
    adapter = LedgerAdapter(
        tenant_txn, harness.payloads, build_connectors(load_providers_config()), cfg
    )
    info = RunInfo(tenant, account, run, T0 - timedelta(hours=1), domain, "Acme")
    inputs = await load_inputs(info, adapter, adapter)
    result = await grade_claims(
        [
            {
                "claim_kind": "sourced",
                "local_id": "j",
                "attribute_key": "jobs.salesforce_admin.open",
                "value": "Senior Salesforce Administrator",
                "evidence": [{"url": CAREERS, "supporting_sentence": SENTENCE}],
            }
        ],
        info,
        inputs,
        cfg,
        T0 + timedelta(hours=1),
        judge=ScriptedJudge([{"supported": True, "reason": "stated"}]),
        judge_profile_ok=True,
    )
    (claim,) = result.graded
    assert claim.product_grade == "confirmed"
    fact = await repo.record_graded_fact(tenant_txn, info, claim)
    return info, inputs, cfg, [(fact, claim)]


def deps(info: RunInfo, reply: dict[str, Any] | None) -> Any:
    async def model(task_kind: str, system: str, user: str) -> LlmResult:
        assert task_kind == "source_reread"
        payload = json.loads(user)
        assert "Senior Salesforce Administrator" not in payload["question"]  # blind (V2)
        if reply is None:
            raise RuntimeError("verifier unavailable")
        return LlmResult(json.dumps(reply), "scripted", LlmUsage(1, 1))

    return SimpleNamespace(
        txn=tenant_txn,
        tenant_id=info.tenant_id,
        run_id=info.run_id,
        router=None,
        model=model,
        clock=SimpleNamespace(now=lambda: T0),
    )


HONEST = {"answer": "Senior Salesforce Administrator", "quote": SENTENCE}


@pytest.mark.parametrize(
    ("reply", "independent", "outcome"),
    [
        (HONEST, True, "confirmed"),
        (
            {
                "answer": "Senior Salesforce Administrator",
                "quote": "Acme is hiring a Senior Salesforce "
                "Administrator to run every system worldwide.",
            },
            True,
            "not_found",
        ),
        ({"answer": "Chief Technology Officer", "quote": SENTENCE}, True, "not_found"),
        (
            # a truncated value whose quote is a real substring of the source: a mismatch
            {"answer": "Senior Salesforce Admin", "quote": "Senior Salesforce Admin"},
            True,
            "value_mismatch",
        ),
        (None, True, "unavailable"),
        (HONEST, False, "not_independent"),
    ],
)
async def test_verification_stage_keeps_or_supersedes(
    harness: Harness, tenant_a: UUID, reply: Any, independent: bool, outcome: str
) -> None:
    info, inputs, cfg, recorded = await confirmed_fact(harness, tenant_a)
    vcfg = load_verify_config()
    counts = await verify_confirmed(
        deps(info, reply), info, recorded, inputs.sources, cfg, vcfg, independent
    )
    assert counts == {f"verify_{outcome}": 1}
    fact_id = recorded[0][0]
    (row,) = admin("SELECT outcome, action FROM verifier_findings WHERE fact_id = %s", fact_id)
    assert row[0] == outcome
    active = admin(
        "SELECT product_grade, may_state_to_prospect FROM facts WHERE run_id = %s"
        " AND superseded_at IS NULL",
        info.run_id,
    )
    if outcome == "confirmed":
        assert active == [("confirmed", True)] and row[1] == "keep"
    else:
        assert active == [("probable", False)] and row[1] == "downgrade"
        events = admin(
            "SELECT from_product, to_product, reason_code FROM grade_events"
            " WHERE run_id = %s AND actor = 'verifier'",
            info.run_id,
        )
        assert events == [("confirmed", "probable", f"verifier_{outcome}")]
    # a resumed pass writes nothing new (V8)
    await verify_confirmed(
        deps(info, reply), info, recorded, inputs.sources, cfg, vcfg, independent
    )
    assert len(admin("SELECT 1 FROM verifier_findings WHERE fact_id = %s", fact_id)) == 1
    assert len(
        admin("SELECT 1 FROM grade_events WHERE run_id = %s AND actor = 'verifier'", info.run_id)
    ) == (0 if outcome == "confirmed" else 1)
