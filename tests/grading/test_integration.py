"""Workflow integration: distill_grade handler through the Stage contract, and the FactRepo port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.integration import (
    DistillGradeDeps,
    GradingFactRepo,
    judge_profile_ok,
    load_run_info,
    make_distill_grade_handler,
)
from account_intelligence.workflow.spec import load_spec
from account_intelligence.workflow.stage import Stage
from tests.conftest import ADMIN_DSN
from tests.connectors.conftest import make_run
from tests.fakes.grading_fakes import FakeLedger, FakeSources, ScriptedJudge
from tests.grading.conftest import NOW
from tests.grading.db_world import graded_confirmed
from tests.grading.world import CAREERS, sourced, world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


class Clock:
    def now(self):  # type: ignore[no-untyped-def]
        return NOW


class Proposals:
    def __init__(self, items: Sequence[Any]) -> None:
        self.items = list(items)

    async def load(self, tenant_id: UUID, run_id: UUID, reference: str) -> Sequence[str | Mapping[str, Any]]:
        assert reference == "proposals/run-1"
        return self.items


def _rebind(w: Any, run: UUID) -> Any:
    from dataclasses import replace

    w.sources = [replace(s, run_id=run) for s in w.sources]
    w.fetches = {k: replace(f, run_id=run) for k, f in w.fetches.items()}
    return w


async def test_distill_grade_stage_persists_and_returns_only_counters(tenant_a: UUID) -> None:
    account, run, _ = make_run(tenant_a, domain="acme.example")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("UPDATE account_runs SET created_at = %s WHERE id = %s", (NOW - timedelta(hours=3), run))
        c.execute("UPDATE accounts SET name = 'Acme', careers_url = 'https://careers.acme.example/', "
                  "careers_url_verified_at = now() WHERE id = %s", (account,))
    info = await load_run_info(tenant_txn, tenant_a, run)
    assert info.primary_hosts == frozenset({"careers.acme.example"}) and info.account_name == "Acme"

    w = _rebind(world(), run)
    proposals = [
        sourced("j", "jobs.salesforce_admin.open", "Senior Salesforce Administrator",
                (CAREERS, "Acme is hiring a Senior Salesforce Administrator to own the CRM platform.")),
        sourced("x", "jobs.fake.role", "Chief Fabricator", ("https://nowhere.example/x", "made up sentence here")),
    ]
    deps = DistillGradeDeps(tenant_txn, FakeSources(w.sources), FakeLedger(w.fetches), Proposals(proposals),
                            load_grading_config(), Clock(), judge=ScriptedJudge([{"supported": True, "reason": "ok"}]))
    spec = next(s for s in load_spec().stages if s.id == "distill_grade")
    out = await Stage(spec, make_distill_grade_handler(deps)).run(
        {"tenant_id": str(tenant_a), "run_id": str(run), "references": {"claim_proposals": "proposals/run-1"}})
    assert out["counters"]["claims_graded"] == 1 and out["counters"]["drop_url_not_in_run"] == 1
    assert out["completed_stages"] == ["distill_grade"] and "Salesforce" not in str(out)
    with psycopg.connect(ADMIN_DSN) as c:
        grade = c.execute("SELECT product_grade, flags FROM facts WHERE run_id = %s", (run,)).fetchone()
        drops = c.execute("SELECT count(*) FROM claim_drops WHERE run_id = %s", (run,)).fetchone()[0]
        pinned = c.execute("SELECT grading_config_hash FROM account_runs WHERE id = %s", (run,)).fetchone()[0]
    # The repository's model config uses one profile for judge and generator: the claim is capped.
    assert judge_profile_ok(load_grading_config()) is False
    assert grade[0] == "probable" and "unjudged" in grade[1]
    assert drops == 1 and pinned == load_grading_config().content_hash
    repo = GradingFactRepo(tenant_txn)
    listed = await repo.list_for_run(str(tenant_a), str(run))
    assert [r["product_grade"] for r in listed] == ["probable"]


async def test_stage_without_proposals_reports_no_progress(tenant_a: UUID) -> None:
    deps = DistillGradeDeps(tenant_txn, FakeSources(), FakeLedger(), Proposals([]), load_grading_config(), Clock())
    out = await make_distill_grade_handler(deps)({"tenant_id": str(tenant_a), "run_id": str(UUID(int=1))})
    assert out["loop_exits"] == {"distill_grade": "no_progress"}


async def test_fact_repo_accepts_only_valid_graded_claims(tenant_a: UUID) -> None:
    account, run, _ = make_run(tenant_a)
    repo = GradingFactRepo(tenant_txn)
    fact_id = await repo.write_with_evidence(str(tenant_a), str(run), graded_confirmed().model_dump(mode="json"))
    assert fact_id
    bad = graded_confirmed().model_dump(mode="json") | {"read_in_primary_this_run": False}
    with pytest.raises(Exception, match="primary read"):
        await repo.write_with_evidence(str(tenant_a), str(run), bad)
    with pytest.raises(LookupError):
        await load_run_info(tenant_txn, tenant_a, UUID(int=7))
