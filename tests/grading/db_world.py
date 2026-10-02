"""Synthetic graded claims and run info for database tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from account_intelligence.grading.ports import RunInfo
from account_intelligence.grading.schema import GradedClaim, GradedEvidence, LeadRecord

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def run_info(tenant: UUID, account: UUID, run: UUID) -> RunInfo:
    return RunInfo(tenant, account, run, WHEN - timedelta(hours=2), "acme.example", "Acme")


def evidence(**over: Any) -> GradedEvidence:
    base: dict[str, Any] = dict(
        evidence_kind="source", url="https://careers.acme.example/jobs/42",
        supporting_sentence="Acme is hiring a Senior Salesforce Administrator to own the CRM platform.",
        span_sha256="a" * 64, source_ref="tenants/t/raw/x.json.gz#0", call_ref="c1", origin="full_page_read",
        tier="T1", lineage_group="account_primary", is_aggregator=False, date_kind="observed",
        evidence_date=WHEN, provider_code="p_scrape",
    )
    base.update(over)
    return GradedEvidence(**base)


def graded_confirmed(**over: Any) -> GradedClaim:
    base: dict[str, Any] = dict(
        schema_version=1, config_hash="cfg", claim_hash="h1", claim_kind="sourced",
        attribute_key="jobs.salesforce_admin.open", value="Senior Salesforce Administrator",
        normalised_value="Senior Salesforce Administrator", product_grade="confirmed", engine_grade="B",
        confidence=0.9, read_in_primary_this_run=True, may_state_to_prospect=True, precedence_rule="primary_read",
        evidence=(evidence(),), confirming_question=None, reasoning=None, flags=(), lead=None,
    )
    base.update(over)
    return GradedClaim(**base)


def graded_lead() -> GradedClaim:
    return graded_confirmed(
        claim_hash="h2", product_grade="lead", engine_grade="D", confidence=0.4, read_in_primary_this_run=False,
        may_state_to_prospect=False, precedence_rule="engine_d_or_secondary_only",
        evidence=(evidence(origin="search_snippet", tier="T4", lineage_group="host:blog.example.net",
                           url="https://blog.example.net/x"),),
        lead=LeadRecord(status="pending", reason=None, accepted_queries=0, tool_categories=()),
    )
