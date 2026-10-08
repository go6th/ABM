"""Oracle accounts: hand-written expected coverage, facts and sense-check findings.

Expectations are derived by hand from ``tests/agents/worlds.py`` (synthetic), not from a run.
"""

from __future__ import annotations

import pytest

from tests.agents import worlds

pytestmark = pytest.mark.asyncio

PEOPLE_ROWS = {
    "key_contact_profiles_posts",
    "people_by_platform",
    "full_profiles_senior_people",
    "posts_priority_people",
}


async def test_rich_account(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.rich())
    h.grant_consent()
    record, state = await h.run("r")
    facts = {(f.attribute, f.grade) for f in h.grading.facts_by_run[record.run_id].values()}
    # scrape rows (job board) are full-page reads -> confirmed; search and signals -> lead
    assert facts == {
        ("launch", "lead"),
        ("revenue", "lead"),
        ("leadership", "lead"),
        ("hiring", "confirmed"),
        ("stack", "lead"),
        ("fit", "lead"),
    }
    assert {p.name for p in h.grading.people[record.run_id]} == {
        "Avery Quinn",
        "Rowan Hale",
        "Sam Ortiz",
    }
    rows = h.tracker.rows_by_run[record.run_id]
    assert len(rows) == 33 and all(r.status == "done" for r in rows.values())
    assert state["outcome"] == "completed" and state["counters"]["sense_findings"] == 0
    assert state["careers_url"] == "https://northwind-synthetic.example/careers"


async def test_sparse_account(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.sparse())
    record, state = await h.run("r")
    facts = [(f.attribute, f.value) for f in h.grading.facts_by_run[record.run_id].values()]
    assert facts == [("launch", "Quietfield announced a regional data platform pilot.")]
    rows = h.tracker.rows_by_run[record.run_id]
    blocked = {k: r.reason for k, r in rows.items() if r.status == "blocked"}
    assert blocked == {
        "earnings_filings": "provider_down",
        **{k: "consent_required" for k in PEOPLE_ROWS},
    }
    assert state["outcome"] == "partial" and h.delivery.released


async def test_tangled_account(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.tangled())
    record, state = await h.run("r")
    facts = h.grading.facts_by_run[record.run_id].values()
    conflicting = sorted(f.value for f in facts if f.grade == "conflicting")
    assert conflicting == [
        "Meridian employs 12,000 people worldwide.",
        "Meridian employs 8,500 people after the spin-off.",
    ]
    # first pass: contradiction, entity boundary, adjacent organisation; the proposal is covered
    # by the approved cap and runs; second pass (record only): entity boundary and adjacent
    assert state["counters"]["sense_findings"] == 5
    assert state["counters"]["targeted_pulls"] == 1
    assert state["exits"]["sense_check"] == "second_pass_recorded"
    assert h.records.sense_saves == 1
