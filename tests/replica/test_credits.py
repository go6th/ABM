"""Research credits and Refresh from web on PostgreSQL through the real application: the monthly
pool, estimates from the account's history, Confirm reserving exactly what the user saw, refusals
(estimate moved, credits short, refresh open, feature off), settling never above the reserve, a
redelivered job charging once, a failed engine releasing everything, and two confirms racing for
the last credits. SYNTHETIC data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs as J
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _admin() -> str:
    from tests.conftest import ADMIN_DSN

    return ADMIN_DSN


def _set(tenant: UUID, sql: str, *args: Any) -> None:
    with psycopg.connect(_admin(), autocommit=True) as c:
        c.execute(sql, (*args, tenant))


def _account(tenant: UUID, domain: str) -> UUID:
    with psycopg.connect(_admin()) as c:
        row = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s AND domain = %s", (tenant, domain)
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def _confirm(plane: Any, domain: str, scope: str, request_id: str | None = None) -> Any:
    """Confirm a refresh with the estimate the Refresh screen shows (as the page does)."""
    view = (await _call(plane, "GET", f"/v1/accounts/{domain}/refresh")).json()
    credits = next(o["credits"] for o in view["options"] if o["scope"] == scope)
    body = {"request_id": request_id or str(uuid4()), "scope": scope, "credits": credits}
    return await _call(plane, "POST", f"/v1/accounts/{domain}/refresh", json=body)


async def _pool(tenant: UUID) -> dict[str, Any]:
    async with tenant_txn(tenant) as s:
        return await CR.pool(s, datetime.now(UTC))


async def test_rates_and_conversion_come_from_one_config() -> None:
    rates = CR.rates()
    assert rates.credit_value_usd > 0
    assert {"jobs", "people", "full", "new_research", "file_public"} <= set(rates.estimates)
    assert CR.usd_to_credits(rates.credit_value_usd) == 1
    assert CR.usd_to_credits(rates.credit_value_usd * Decimal("1.01")) == 2
    assert CR.usd_to_credits(0) == 0
    assert CR.resets_on(datetime(2026, 12, 9, tzinfo=UTC)).isoformat() == "2027-01-01"


async def test_pool_reads_what_the_seed_used_this_month(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    got = (await _call(plane, "GET", "/v1/credits")).json()
    assert got["pool"] == 2000 and got["held"] == 0
    assert got["left"] == got["pool"] - got["used"]


async def test_refresh_options_use_the_accounts_history(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    view = (await _call(plane, "GET", "/v1/accounts/highmark.example/refresh")).json()
    by = {o["scope"]: o for o in view["options"]}
    assert by["jobs"]["credits"] == CR.rates().estimates["jobs"].credits
    assert by["full"]["credits"] == 120  # Highmark's last full refresh used 120
    assert view["allowed"] is True and view["open_job"] is None
    assert view["refreshed_days_ago"] == 2


async def test_confirm_reserves_what_the_user_saw_then_settles(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    before = await _pool(world["a"])
    rid = str(uuid4())
    r = await _confirm(plane, "medica.example", "jobs", rid)
    assert r.status_code == 201, r.text
    job = r.json()
    reserved = job["credits"]
    held = await _pool(world["a"])
    assert held["held"] == reserved and held["left"] == before["left"] - reserved
    again = await _confirm(plane, "medica.example", "jobs", rid)
    assert again.json()["job_id"] == job["job_id"]
    assert (await _pool(world["a"]))["held"] == reserved
    assert await J.run_job(world["a"], UUID(job["job_id"]), J.HermeticEngine()) == "done"
    after = await _pool(world["a"])
    charged = after["used"] - before["used"]
    assert after["held"] == 0 and 0 < charged <= reserved
    notes = (await _call(plane, "GET", "/v1/notifications")).json()
    assert notes["notifications"][0]["kind"] == "refresh_finished"
    assert notes["notifications"][0]["credits_used"] == charged
    other = _plane(world["a"], "someone-else", world["payloads"])
    assert (await _call(other, "GET", "/v1/notifications")).json()["notifications"] == []


async def test_a_moved_estimate_is_shown_again_not_charged(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    view = (await _call(plane, "GET", "/v1/accounts/cambiahealth.example/refresh")).json()
    people = next(o["credits"] for o in view["options"] if o["scope"] == "people")
    body = {"request_id": str(uuid4()), "scope": "people", "credits": people - 1}
    r = await _call(plane, "POST", "/v1/accounts/cambiahealth.example/refresh", json=body)
    assert r.status_code == 409 and r.json()["type"].endswith("estimate_changed")
    assert r.json()["context"]["credits"] == people
    assert (await _pool(world["a"]))["held"] == 0


async def test_confirm_is_refused_when_credits_are_short(world: dict[str, Any]) -> None:
    plane = _plane(world["b"], "other-test", world["payloads"])
    _set(world["b"], "UPDATE client_settings SET monthly_credit_pool = %s WHERE tenant_id = %s", 10)
    try:
        view = (await _call(plane, "GET", "/v1/accounts/cigna.example/refresh")).json()
        assert not any(o["affordable"] for o in view["options"])
        r = await _confirm(plane, "cigna.example", "jobs")
        assert r.status_code == 409 and r.json()["type"].endswith("credits_short")
        ctx = r.json()["context"]
        assert ctx["left"] == view["credits"]["left"] and ctx["needed"] > ctx["left"]
    finally:
        _set(
            world["b"],
            "UPDATE client_settings SET monthly_credit_pool = %s WHERE tenant_id = %s",
            3000,
        )


async def test_refresh_is_refused_when_the_feature_is_off(world: dict[str, Any]) -> None:
    plane = _plane(world["b"], "other-test", world["payloads"])
    _set(
        world["b"],
        "UPDATE client_settings SET web_refresh_allowed = %s WHERE tenant_id = %s",
        False,
    )
    try:
        view = (await _call(plane, "GET", "/v1/accounts/cigna.example/refresh")).json()
        assert view["allowed"] is False
        assert (await _confirm(plane, "cigna.example", "jobs")).status_code == 403
    finally:
        _set(
            world["b"],
            "UPDATE client_settings SET web_refresh_allowed = %s WHERE tenant_id = %s",
            True,
        )


async def test_one_open_refresh_per_account(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    first = await _confirm(plane, "cigna.example", "jobs")
    assert first.status_code == 201
    second = await _confirm(plane, "cigna.example", "people")
    assert second.status_code == 409 and second.json()["type"].endswith("refresh_in_progress")
    ws = (await _call(plane, "GET", "/v1/accounts/cigna.example/workspace")).json()
    assert ws["open_job"]["status"] == "queued" and ws["state"] == "researching"
    await J.run_job(world["a"], UUID(first.json()["job_id"]), J.HermeticEngine())


async def test_a_redelivered_job_runs_and_charges_once(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _confirm(plane, "highmark.example", "jobs")
    job = UUID(r.json()["job_id"])
    before = await _pool(world["a"])
    runs = [J.run_job(world["a"], job, J.HermeticEngine()) for _ in range(3)]
    assert sorted(await asyncio.gather(*runs)) == ["done", "skipped", "skipped"]
    after = await _pool(world["a"])
    expected = CR.usd_to_credits(r.json()["credits"] * Decimal("0.8") * CR.rates().credit_value_usd)
    assert after["used"] - before["used"] == expected and after["held"] == 0


async def test_settle_never_charges_more_than_was_reserved(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _confirm(plane, "highmark.example", "jobs")
    before = await _pool(world["a"])
    await J.run_job(world["a"], UUID(r.json()["job_id"]), J.HermeticEngine(share=Decimal(5)))
    after = await _pool(world["a"])
    assert after["used"] - before["used"] == r.json()["credits"] and after["held"] == 0


async def test_an_engine_that_cannot_run_releases_everything(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _confirm(plane, "medica.example", "people")
    before = await _pool(world["a"])
    assert await J.run_job(world["a"], UUID(r.json()["job_id"]), J.SkillEngine()) == "failed"
    after = await _pool(world["a"])
    assert after["used"] == before["used"] and after["held"] == before["held"] - r.json()["credits"]
    note = (await _call(plane, "GET", "/v1/notifications")).json()["notifications"][0]
    assert note["kind"] == "research_failed" and "not set up" in note["reason"]


async def test_two_confirms_cannot_spend_the_same_credits(world: dict[str, Any]) -> None:
    """Two accounts confirm at once with credits for only one: exactly one reservation wins."""
    tenant = world["b"]
    with psycopg.connect(_admin(), autocommit=True) as c:
        used = c.execute(
            "SELECT coalesce(sum(credits) FILTER (WHERE kind = 'settle'), 0) FROM credit_entries"
            " WHERE tenant_id = %s AND period = %s",
            (tenant, CR.period_of(datetime.now(UTC))),
        ).fetchone()
    assert used is not None
    _set(
        tenant,
        "UPDATE client_settings SET monthly_credit_pool = %s WHERE tenant_id = %s",
        int(used[0]) + 30,
    )
    seed_accounts = []
    with psycopg.connect(_admin(), autocommit=True) as c:
        for name in ("race-one", "race-two"):
            row = c.execute(
                "INSERT INTO accounts (tenant_id, domain, name, added_by) VALUES (%s, %s, %s,"
                " 'other-test')"
                " RETURNING id",
                (tenant, f"{name}.example", name),
            ).fetchone()
            assert row is not None
            seed_accounts.append(UUID(str(row[0])))

    async def confirm(account: UUID) -> str:
        try:
            async with tenant_txn(tenant) as s:
                await CR.reserve(
                    s,
                    account_id=account,
                    kind="web_refresh",
                    scope="jobs",
                    credits=20,
                    actor="other-test",
                    now=datetime.now(UTC),
                )
            return "reserved"
        except CR.CreditsShort:
            return "short"

    try:
        outcomes = await asyncio.gather(*(confirm(a) for a in seed_accounts))
        assert sorted(outcomes) == ["reserved", "short"]
        assert (await _pool(tenant))["held"] == 20
    finally:
        _set(
            tenant, "UPDATE client_settings SET monthly_credit_pool = %s WHERE tenant_id = %s", 3000
        )


async def test_research_on_a_typed_topic_reserves_its_own_estimate(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    view = (await _call(plane, "GET", "/v1/accounts/cambiahealth.example/refresh")).json()
    assert view["topic"]["credits"] == CR.rates().estimates["topic"].credits
    body = {
        "request_id": str(uuid4()),
        "scope": "topic",
        "credits": view["topic"]["credits"],
        "topic": "  Their   ServiceNow renewal timing ",
    }
    r = await _call(plane, "POST", "/v1/accounts/cambiahealth.example/refresh", json=body)
    assert r.status_code == 201, r.text
    job = UUID(r.json()["job_id"])
    ws = (await _call(plane, "GET", "/v1/accounts/cambiahealth.example/workspace")).json()
    assert ws["open_job"]["scope"] == "topic"
    assert ws["open_job"]["topic"] == "Their ServiceNow renewal timing"
    assert await J.run_job(world["a"], job, J.HermeticEngine()) == "done"
    with psycopg.connect(_admin()) as c:
        row = c.execute(
            "SELECT r.run_input->>'topic', j.topic FROM research_jobs j JOIN account_runs r ON"
            " r.id = j.run_id"
            " WHERE j.id = %s",
            (job,),
        ).fetchone()
    assert row == ("Their ServiceNow renewal timing", "Their ServiceNow renewal timing")


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "topic", "credits": 40},
        {"scope": "topic", "credits": 40, "topic": "ab"},
        {"scope": "topic", "credits": 40, "topic": "x" * 301},
        {"scope": "jobs", "credits": 20, "topic": "not for this scope"},
    ],
)
async def test_a_topic_must_be_given_for_topic_research_only(
    world: dict[str, Any], body: dict[str, Any]
) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _call(
        plane,
        "POST",
        "/v1/accounts/highmark.example/refresh",
        json={"request_id": str(uuid4()), **body},
    )
    assert r.status_code == 422
