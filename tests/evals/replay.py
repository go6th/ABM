"""The replay executor (D-50 ``replay`` mode): runs the real pipeline on a synthetic golden account
through the system harness (scripted providers from cassettes, scripted model outputs, real
Postgres, real LangGraph checkpointer) and reads the case output back from the stored rows only.
No network, no spend."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from account_intelligence.tracker.config import load_checklist
from evalgate import metrics
from evalgate.golden import GoldenAccount, GoldenSet, load
from evalgate.runner import CaseOutput
from tests.system.world import CANARY, admin, start_run

LABELS = Path(__file__).resolve().parents[2] / "evals/golden/labels"


def golden_set(today: date) -> GoldenSet:
    """The synthetic golden set."""
    return load(sorted(LABELS.glob("*.yaml")), today, 10_000)


class ReplayExecutor:
    """``evalgate.runner.Executor`` over the system harness."""

    def __init__(self, harness_factory: Any) -> None:
        self.harness_factory = harness_factory

    async def run(self, account: GoldenAccount, repeat: int) -> CaseOutput:
        """Execute one run and read its stored rows."""
        harness = self.harness_factory()
        run = await start_run(harness)
        await run.scheduler.drain()
        facts = {
            r[0]: (str((r[1] or {}).get("value", "")), r[2], None if r[3] is None else float(r[3]))
            for r in admin(
                "SELECT attribute_key, value_json, product_grade, confidence FROM facts"
                " WHERE run_id = %s AND superseded_at IS NULL",
                run.run,
            )
        }
        citations = tuple(
            metrics.Citation(bool(r[0]), bool(r[1]))
            for r in admin(
                "SELECT EXISTS (SELECT 1 FROM tool_calls t WHERE t.run_id = f.run_id"
                " AND t.args_hash = e.call_ref), e.span_sha256 IS NOT NULL FROM fact_evidence e"
                " JOIN facts f ON f.id = e.fact_id WHERE f.run_id = %s"
                " AND e.evidence_kind = 'source'",
                run.run,
            )
        )
        drops = admin("SELECT count(*) FROM claim_drops WHERE run_id = %s", run.run)[0][0]
        high = {r.key for r in load_checklist().rows if r.priority == "high"}
        rows = dict(admin("SELECT row_key, status FROM coverage_rows WHERE run_id = %s", run.run))
        costs = tuple(
            Decimal(r[0]) if r[0] is not None else None
            for r in admin("SELECT cost_usd FROM tool_calls WHERE run_id = %s", run.run)
        )
        span = admin(
            "SELECT EXTRACT(EPOCH FROM (finished_at - created_at)) FROM account_runs WHERE id = %s",
            run.run,
        )[0][0]
        texts = [v for v, _, _ in facts.values()] + [
            r[0]
            for r in admin(
                "SELECT e.supporting_sentence FROM fact_evidence e JOIN facts f ON f.id = e.fact_id"
                " WHERE f.run_id = %s",
                run.run,
            )
        ]
        leaked = sum(1 for t in texts if CANARY.casefold() in t.casefold())
        return CaseOutput(
            facts=facts,
            citations=citations,
            supported=(True,) * len(facts) + (False,) * int(drops),
            high_rows={k: v for k, v in rows.items() if k in high},
            costs=costs,
            duration_s=None if span is None else float(span),
            security_failures=leaked,
            security_checks=1,
        )
