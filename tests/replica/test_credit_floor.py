"""The budget floor (known problems 16, e2e test D-5): with the caps on, an estimate built from
the account's earlier jobs never falls below half of the configured rate of its scope, and a
resume reserves for the steps the stopped run has left, never one credit. With the caps off
(``RESEARCH_CAPS=off``) nothing changes: the history alone decides. SYNTHETIC data only."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from tests.replica.test_resume_research import _call, _db, _plane, _run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

FULL_RATE = 250  # config/credits.yaml, scope "full"


def _account_of(run: UUID) -> UUID:
    return UUID(str(_db("SELECT account_id FROM account_runs WHERE id = %s", run)[0][0]))


def _past(tenant: UUID, account: UUID, scope: str, used: list[int]) -> None:
    """Finished jobs of the account (newest last), each having used ``used`` credits."""
    for n, credits in enumerate(used):
        _db(
            "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
            " credits_estimated, credits_used, requested_by, owner_user_id, created_at,"
            " finished_at) VALUES (%s, %s, 'web_refresh', %s, 'done', to_char(now(), 'YYYY-MM'),"
            " %s, %s, 'priya-test', 'priya-test', now() - make_interval(hours => %s),"
            " now() - make_interval(hours => %s))",
            tenant,
            account,
            scope,
            credits,
            credits,
            10 - n,
            10 - n,
        )


async def _estimate(tenant: UUID, account: UUID, scope: str = "full") -> int:
    async with tenant_txn(tenant) as s:
        return (await CR.estimate(s, account, scope)).credits


async def _estimate_resume(tenant: UUID, run: UUID, scope: str = "full") -> int:
    async with tenant_txn(tenant) as s:
        return (await CR.estimate_resume(s, _account_of(run), scope, run)).credits


async def test_a_cheap_earlier_run_no_longer_lowers_the_estimate_below_half_the_rate(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The e2e scenario: a full refresh that used 29 credits made the next one reserve 29."""
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    user = f"floor-{uuid4().hex[:6]}"
    run, _ = _run(world["a"], user, "PARTIAL")
    account = _account_of(run)
    _past(world["a"], account, "full", [29])
    assert CR.floor_credits("full") == FULL_RATE // 2
    assert await _estimate(world["a"], account) == FULL_RATE // 2  # was 29
    # a history above the floor still decides: the floor only stops the shrinking
    other, _ = _run(world["a"], user, "PARTIAL")
    _past(world["a"], _account_of(other), "full", [180, 200, 160])
    assert await _estimate(world["a"], _account_of(other)) == 180
    # no history: the configured rate, as before
    fresh, _ = _run(world["a"], user, "PARTIAL")
    assert await _estimate(world["a"], _account_of(fresh)) == FULL_RATE


async def test_with_the_caps_off_the_history_alone_decides(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_CAPS", "off")
    user = f"floor-off-{uuid4().hex[:6]}"
    run, _ = _run(world["a"], user, "PARTIAL")
    account = _account_of(run)
    _past(world["a"], account, "full", [29])
    assert await _estimate(world["a"], account) == 29
    assert await _estimate_resume(world["a"], run) == 29  # the plain estimate, unchanged


async def test_a_resume_reserves_for_the_steps_it_has_left_never_one_credit(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zephyr (e2e D-5): a refresh used 29 credits, a resume then reserved 1 and stalled."""
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    user = f"floor-res-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    run, _ = _run(world["a"], user, "PARTIAL")  # three planned steps, two done
    account = _account_of(run)
    _past(world["a"], account, "full", [29, 1])  # a cheap run, then a resume that reserved 1
    out = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(run), "request_id": str(uuid4())}
    )
    assert out.status_code == 201, out.text
    (reserved,) = _db(
        "SELECT credits_estimated FROM research_jobs WHERE id = %s", out.json()["job_id"]
    )[0]
    assert reserved >= FULL_RATE // 2 > 1
    assert _db(
        "SELECT credits FROM credit_entries WHERE job_id = %s AND kind = 'reserve'",
        out.json()["job_id"],
    ) == [(reserved,)]


async def test_a_resume_follows_the_share_of_steps_left_above_the_floor(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    user = f"floor-share-{uuid4().hex[:6]}"
    run, _ = _run(world["a"], user, "PARTIAL")  # planned: company_news, job_postings, people
    account = _account_of(run)
    _past(world["a"], account, "full", [300, 300, 300])  # whole = 300 credits
    assert await _estimate(world["a"], account) == 300
    # one of three steps left: a third is 100, the floor (125) wins
    assert await _estimate_resume(world["a"], run) == FULL_RATE // 2
    # two of three left: 200
    _db(
        "UPDATE coverage_rows SET status = NULL WHERE run_id = %s AND row_key = 'job_postings'", run
    )
    assert await _estimate_resume(world["a"], run) == 200
    # nothing done: the whole estimate
    _db(
        "UPDATE coverage_rows SET status = NULL WHERE run_id = %s AND row_key = 'company_news'", run
    )
    assert await _estimate_resume(world["a"], run) == 300


async def test_a_run_without_coverage_rows_is_resumed_whole(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    user = f"floor-none-{uuid4().hex[:6]}"
    run, _ = _run(world["a"], user, "PARTIAL")
    _db("DELETE FROM coverage_rows WHERE run_id = %s", run)
    assert await _estimate_resume(world["a"], run) == FULL_RATE
    async with tenant_txn(world["a"]) as s:
        assert await CR.steps_left(s, run) == (0, 0)


async def test_the_refresh_page_shows_the_floored_estimate_it_reserves(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the page shows and what Confirm reserves come from the one estimate."""
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    user = f"floor-page-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    run, domain = _run(world["a"], user, "PARTIAL")
    _past(world["a"], _account_of(run), "full", [29])
    view = (await _call(plane, "GET", f"/v1/accounts/{domain}/refresh")).json()
    shown = next(o["credits"] for o in view["options"] if o["scope"] == "full")
    assert shown == FULL_RATE // 2


async def test_the_planned_rule_is_the_one_the_account_page_uses() -> None:
    from account_intelligence.replica.workspace import PLANNED

    assert CR.PLANNED == PLANNED
