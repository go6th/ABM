"""Hand-written oracles (spec 9.2): three synthetic runs (within the cap, cap reached with partial
results, an extension approved) driven through the real connector entry and ledger on Postgres;
the stored spend report is compared field by field with ``oracles.json`` (worked out by hand)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import BudgetExceeded
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.db.session import tenant_txn
from spend import service
from spend.config import SpendConfig
from spend.models import PlanItem, Principal
from tests.connectors.conftest import Reply, context
from tests.fakes.spend import COSTS, FakeBalances
from tests.spend.conftest import NOW, admin, capped_run
from tests.spend.test_connector_gate import _harness

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
ORACLES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/synthetic/spend/oracles.json").read_text()
)
STAGES = ("setup", "news")


def _plan(calls: int) -> tuple[PlanItem, ...]:
    return (
        PlanItem(
            row_key="company_news",
            stage="news",
            priority="High",
            provider_code="p_search",
            operation="web_search",
            calls=calls,
            mandatory=True,
        ),
    )


def _human(tenant: UUID) -> Principal:
    return Principal(kind="human", subject="u", tenant_id=str(tenant), role="admin")


async def _approved_run(
    tenant: UUID, cfg: SpendConfig, calls: int, **kw: Any
) -> tuple[UUID, UUID, str, int]:
    account, run, domain = capped_run(tenant, **kw)
    p = await service.propose(
        tenant_txn,
        tenant,
        run,
        _plan(calls),
        COSTS,
        FakeBalances({"p_search": "ok"}, NOW),
        cfg,
        NOW,
        STAGES,
    )
    cap = await service.approve(
        tenant_txn, tenant, run, p.version, p.proposal.content_hash, _human(tenant), cfg, NOW
    )
    return account, run, domain, cap


async def _search(ctx: Any, rt: Any, i: int) -> bool:
    """One paid search with a distinct query; False when the cap refused it."""
    try:
        await call_operation(
            Operation.WEB_SEARCH, WebSearchArgs(queries=(f"synthetic q{i}",)), ctx, rt
        )
    except BudgetExceeded:
        return False
    return True


async def _report(tenant: UUID, run: UUID, cfg: SpendConfig) -> dict[str, Any]:
    report, _, _ = await service.close_run(tenant_txn, tenant, run, cfg, NOW, 300)
    stored = admin("SELECT operator_json, status FROM spend_reports WHERE run_id = %s", run)
    assert len(stored) == 1 and stored[0][0] == report.body and stored[0][1] == report.status
    return {"body": report.body, "status": report.status}


def _compare(got: dict[str, Any], want: dict[str, Any]) -> None:
    body = got["body"]
    assert body["totals"] == want["totals"]
    line = next(x for x in body["by_provider"] if x["key"] == "p_search")
    assert {k: line[k] for k in want["p_search"]} == want["p_search"]
    assert got["status"] == want["status"]
    assert body["extensions"] == want["extensions"]
    assert len(body["cap_versions"]) == want["cap_versions"]
    assert body["reconciliation"] == ["consistent"]


async def test_oracle_within_the_cap(tenant_a: UUID, cfg: SpendConfig) -> None:
    want = ORACLES["within_cap"]
    account, run, domain, cap = await _approved_run(tenant_a, cfg, want["plan_calls"])
    assert cap == want["cap_micros"]
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].fallback = Reply.cassette("p_search", "web_search")
    ctx, rt = context(tenant_a, account, run, domain), harness.runtime()
    for i in range(want["paid"]):
        assert await _search(ctx, rt, i)
    for i in range(want["replays"]):
        assert await _search(ctx, rt, i)  # same query: a replay, free (K8)
    assert harness.providers[ProviderCode.P_SEARCH].calls == want["paid"]
    _compare(await _report(tenant_a, run, cfg), want)


async def test_oracle_cap_reached_with_partial_results(tenant_a: UUID, cfg: SpendConfig) -> None:
    want = ORACLES["cap_reached"]
    account, run, domain, cap = await _approved_run(tenant_a, cfg, want["plan_calls"])
    assert cap == want["cap_micros"]
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].fallback = Reply.cassette("p_search", "web_search")
    ctx, rt = context(tenant_a, account, run, domain), harness.runtime()
    done = [await _search(ctx, rt, i) for i in range(want["attempted"])]
    assert done.count(True) == want["paid"] and done[-1] is False
    assert harness.providers[ProviderCode.P_SEARCH].calls == want["paid"]
    _compare(await _report(tenant_a, run, cfg), want)


async def test_oracle_extension_approved(tenant_a: UUID, cfg: SpendConfig) -> None:
    want = ORACLES["extension"]
    account, run, domain, _ = await _approved_run(tenant_a, cfg, want["plan_calls"], attended=True)
    harness = _harness()
    harness.providers[ProviderCode.P_SEARCH].fallback = Reply.cassette("p_search", "web_search")
    ctx, rt = context(tenant_a, account, run, domain), harness.runtime()
    assert all([await _search(ctx, rt, i) for i in range(want["paid_before"])])
    assert not await _search(ctx, rt, 99)  # at the cap: refused until the extension
    version = await service.request_extension(
        tenant_txn, tenant_a, run, want["extension_micros"], "synthetic", cfg
    )
    digest = admin(
        "SELECT proposal_hash FROM run_cap_versions WHERE run_id = %s AND version = %s",
        run,
        version,
    )[0][0]
    await service.approve(tenant_txn, tenant_a, run, version, digest, _human(tenant_a), cfg, NOW)
    for i in range(100, 100 + want["paid_after"]):
        assert await _search(ctx, rt, i)
    got = await _report(tenant_a, run, cfg)
    _compare(got, want)
    assert [v["cap_usd"] for v in got["body"]["cap_versions"]] == want["cap_version_amounts"]
    assert all(v["approved_by"] == "human:u" for v in got["body"]["cap_versions"])
