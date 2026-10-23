"""The pull follows the owner's skill (audit 2026-10-21): each searched row sends the skill's own
queries instead of the row's label, the budget goes to people, then feeds, then searches, and an
internal-history row is never searched on the open web."""

from __future__ import annotations

from typing import Any

import pytest

from agents.config import AgentConfigInvalid, _row_queries, load_skill_config
from agents.graph import _plan_order
from agents.guards import ACCOUNT_KEYED_NEEDS
from tests.agents.conftest import Harness

CFG = load_skill_config()


def test_every_open_web_row_has_the_skills_own_queries() -> None:
    searched = {
        k
        for k, r in CFG.plan.items()
        if r.need in ("web_search", "page_read", "scrape")
        and r.step != "people"
        and not r.internal_history
    }
    missing = {k for k in searched if not CFG.plan[k].queries}
    assert not missing, missing
    for key in searched:
        assert len(set(CFG.plan[key].queries)) >= CFG.min_phrasings  # G3: three phrasings
        for q in CFG.plan[key].queries:
            text = q.format(account="Acme Corp", domain="acme.example", year=2026, prev_year=2025)
            assert len(text) <= 200 and "{" not in text


def test_a_bad_query_template_is_refused_at_load() -> None:
    plan = {"queries": {"value": {"podcasts": ["{account} a", "{account} b", "{topic} c"]}}}
    with pytest.raises(AgentConfigInvalid, match="bad template"):
        _row_queries(plan, 3)
    with pytest.raises(AgentConfigInvalid, match="fewer than 3"):
        _row_queries({"queries": {"value": {"podcasts": ["{account} a", "{account} a"]}}}, 3)


def test_the_pull_spends_on_people_then_feeds_then_searches() -> None:
    order = _plan_order(CFG)

    def rank(key: str) -> int:
        row = CFG.plan[key]
        if row.step == "people" or row.need in ("people_by_title", "people_by_technology"):
            return 0
        return 1 if row.need in ACCOUNT_KEYED_NEEDS else 2

    ranks = [rank(k) for k in order]
    assert ranks == sorted(ranks)
    assert order.index("job_postings_12_months") < order.index("basic_fit_check")
    assert order.index("technology_detections") < order.index("connections_alumni")


@pytest.mark.asyncio
async def test_rows_send_the_skills_queries_and_internal_history_is_not_searched(
    h: Harness,
) -> None:
    sent: list[tuple[str, str]] = []
    real = h.provider.call

    async def call(ctx: Any, need: str, query: str, row_key: str) -> Any:
        sent.append((row_key, query))
        return await real(ctx, need, query, row_key)

    h.provider.call = call  # type: ignore[method-assign]
    record, _ = await h.run("r")
    by_row: dict[str, list[str]] = {}
    for row, query in sent:
        by_row.setdefault(row, []).append(query)
    name = h.world.name
    assert by_row["vendor_customer_stories"] == [
        q.format(account=name, domain=h.world.domain, year=2026, prev_year=2025)
        for q in CFG.plan["vendor_customer_stories"].queries
    ]
    assert "prior_customer_status" not in by_row and "connections_alumni" not in by_row
    rows = {r.row_key: r for r in h.manifests.by_run[record.run_id].rows}
    assert rows["prior_customer_status"].blocked_reason == "no_internal_access"


def test_a_topic_refresh_still_searches_the_users_words() -> None:
    from uuid import uuid4

    from agents.ports import RunContext
    from agents.pull.agent import phrasings_for

    ctx = RunContext(uuid4(), uuid4(), "k", "Acme", "acme.example", False, CFG.config_version)
    row = CFG.plan["podcasts"]
    assert all(
        "cloud migration" in q for q in phrasings_for(CFG, row, ctx, 2026, "cloud migration")
    )
    assert phrasings_for(CFG, row, ctx, 2026)[0] == "Acme podcast interview"


def test_a_research_goal_adds_searches_and_keeps_the_rows_own() -> None:
    """Owner decision 2026-10-23: a goal ADDS one search per goal row after the row's own
    queries, never replacing them; the goal rows come from research_scopes.yaml."""
    from uuid import uuid4

    from account_intelligence.replica.jobs import goal_rows, scope_rows
    from agents.ports import RunContext
    from agents.pull.agent import GOAL_PHRASINGS, phrasings_for

    ctx = RunContext(uuid4(), uuid4(), "k", "Acme", "acme.example", False, CFG.config_version)
    assert goal_rows() == scope_rows()["topic"]
    for key in goal_rows():
        row = CFG.plan[key]
        own = phrasings_for(CFG, row, ctx, 2026)
        aimed = phrasings_for(CFG, row, ctx, 2026, goal="  their   HR stack ")
        assert aimed[: len(own)] == own
        assert aimed[len(own) :] == ("Acme their HR stack",)
        assert len(aimed) - len(own) == len(GOAL_PHRASINGS)
    long = phrasings_for(CFG, CFG.plan["company_news"], ctx, 2026, goal="y" * 500)
    assert len(long[-1]) == len("Acme ") + 200


@pytest.mark.asyncio
async def test_people_rows_are_not_paid_for_while_people_cannot_be_saved(h: Harness) -> None:
    """With consent, a people-step row is skipped (no call) while grading cannot record people;
    without consent the reason stays consent_required (the one the client can act on)."""
    h.grading.records_people = False  # type: ignore[attr-defined]
    sent: list[str] = []
    real = h.provider.call

    async def call(ctx: Any, need: str, query: str, row_key: str) -> Any:
        sent.append(row_key)
        return await real(ctx, need, query, row_key)

    h.provider.call = call  # type: ignore[method-assign]
    h.grant_consent()
    record, _ = await h.run("with-consent")
    rows = {r.row_key: r for r in h.manifests.by_run[record.run_id].rows}
    people_step = [k for k, r in CFG.plan.items() if r.step == "people"]
    assert people_step and not set(people_step) & set(sent)
    assert all(rows[k].blocked_reason == "people_not_recorded" for k in people_step)


@pytest.mark.asyncio
async def test_without_consent_people_rows_still_say_consent(h: Harness) -> None:
    h.grading.records_people = False  # type: ignore[attr-defined]
    record, _ = await h.run("no-consent")
    rows = {r.row_key: r for r in h.manifests.by_run[record.run_id].rows}
    people_step = [k for k, r in CFG.plan.items() if r.step == "people"]
    assert all(rows[k].blocked_reason == "consent_required" for k in people_step)
