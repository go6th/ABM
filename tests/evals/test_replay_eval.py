"""The replay eval over the synthetic golden set (D-50): the real pipeline runs twice per
account, the metrics come only from stored rows, the report is written and the gate decides.
Synthetic labels never pass a gate: the expected verdict is BLOCK on the thresholds, never on a
missing mode, a security failure or flakiness."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from evalgate import gates, report, runner
from evalgate.baseline import Provenance
from evalgate.config import load_eval_config
from tests.connectors.conftest import Harness
from tests.evals.replay import ReplayExecutor, golden_set

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio, pytest.mark.eval]


def make_harness() -> Harness:
    from account_intelligence.connectors.base import ProviderCode
    from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, InMemoryPayloadStore

    clock = FakeClock()
    return Harness(
        clock=clock,
        sleep=FakeSleep(clock),
        providers={c: FakeProvider(clock) for c in ProviderCode},
        payloads=InMemoryPayloadStore(),
        resolver_table={},
    )


async def test_replay_eval_over_the_synthetic_golden_set(tmp_path: Path) -> None:
    cfg = load_eval_config()
    gs = golden_set(date(2026, 10, 6))
    result = await runner.run(gs, ReplayExecutor(make_harness), 2, cfg.calibration_bins)
    assert result.metrics["security_pass_rate"] == 1.0  # no canary in any stored output
    flaky = gates.flaky_metrics(
        {
            m: [
                [c.values[m] for c in result.cases if c.account == a and c.values[m] is not None]
                for a in {c.account for c in result.cases}
            ]
            for m in ("coverage", "citation_validity", "unsupported_claim_rate")
        }
    )
    assert flaky == frozenset()  # replay is deterministic on identical input (E6)
    verdict = gates.evaluate(cfg, "pull_request", {"replay": "ran"}, result.metrics, None, {})
    prov = Provenance(
        os.environ.get("GITHUB_SHA", "local"), "replay", "replay", gs.version, "replay"
    )
    out = Path(os.environ.get("EVAL_REPORT_DIR", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval-report.md").write_text(report.markdown(result, verdict, prov, cfg.alpha))
    (out / "eval-report.json").write_text(report.as_json(result, verdict, prov))
    assert verdict.status == "block"  # synthetic labels never pass the ARCH thresholds
    assert not any(r.startswith(("mode ", "security")) for r in verdict.reasons)
