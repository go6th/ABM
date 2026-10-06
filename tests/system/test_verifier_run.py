"""The verifier on the whole run path (D-49): after distill, no active Confirmed fact exists
without a ``confirmed`` verifier finding for it (V4), every disagreement is a finding row (V10),
and a lying generator's fabrications never survive as Confirmed.

GUARANTEE(test_fabricated_confirmed_claims_are_downgraded_in_a_whole_run)
"""

from __future__ import annotations

import pytest

from tests.connectors.conftest import Harness
from tests.system.world import Script, admin, start_run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_fabricated_confirmed_claims_are_downgraded_in_a_whole_run(harness: Harness) -> None:
    run = await start_run(harness, Script(lie=True))
    await run.scheduler.drain()
    unverified = admin(
        "SELECT f.id FROM facts f WHERE f.run_id = %s AND f.superseded_at IS NULL"
        " AND f.product_grade = 'confirmed' AND NOT EXISTS (SELECT 1 FROM verifier_findings v"
        " WHERE v.fact_id = f.id AND v.outcome = 'confirmed')",
        run.run,
    )
    assert unverified == []  # V4: never delivered as Confirmed without a confirmed re-read
    findings = admin("SELECT outcome, action FROM verifier_findings WHERE run_id = %s", run.run)
    for outcome, action in findings:
        assert (action == "keep") == (outcome == "confirmed")
    superseded = admin(
        "SELECT count(*) FROM grade_events WHERE run_id = %s AND actor = 'verifier'", run.run
    )[0][0]
    assert superseded == sum(1 for o, _ in findings if o != "confirmed")
