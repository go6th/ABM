"""Schedules and the scheduler tick on PostgreSQL through the real application: next refresh and
the monthly estimate, saving immediately, refusing a cadence faster than the client allows, a due
refresh starting once from the pool, a short pool leaving it waiting (with the owner told) until
the pool has room, and the cross-tenant lookups returning identifiers only. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.replica import credits as CR
from account_intelligence.replica import schedules as SC
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _admin() -> str:
    from tests.conftest import ADMIN_DSN

    return ADMIN_DSN


def _sql(sql: str, *args: Any) -> list[Any]:
    with psycopg.connect(_admin(), autocommit=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []


def _account(tenant: UUID, domain: str) -> UUID:
    return UUID(
        str(
            _sql("SELECT id FROM accounts WHERE tenant_id = %s AND domain = %s", tenant, domain)[0][
                0
            ]
        )
    )


class Recorder:
    """Records the jobs the tick starts (instead of running them)."""

    def __init__(self) -> None:
        self.started: list[tuple[UUID, UUID]] = []

    async def __call__(self, tenant: UUID, job: UUID) -> None:
        self.started.append((tenant, job))


async def test_view_shows_next_refresh_and_the_monthly_estimate(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    view = (await _call(plane, "GET", "/v1/schedules")).json()
    rows = {r["name"]: r for r in view["rows"]}
    assert (
        rows["Highmark"]["cadence"] == "weekly"
        and rows["Highmark"]["next_label"] == "Next refresh in 5 days"
    )
    assert rows["Medica"]["next_label"] == "No scheduled refresh"
    assert [o["label"] for o in rows["Highmark"]["options"]] == [
        "3 days",
        "Weekly",
        "2 weeks",
        "Monthly",
        "Off",
    ]
    assert view["pool"] == 2000 and view["estimate_month"] % 10 == 0 and view["estimate_month"] > 0


async def test_saving_a_cadence_updates_next_refresh_and_estimate(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    before = (await _call(plane, "GET", "/v1/schedules")).json()
    cambia = _account(world["a"], "cambiahealth.example")
    r = await _call(plane, "PUT", f"/v1/schedules/{cambia}", json={"cadence": "monthly"})
    assert r.status_code == 200, r.text
    after = r.json()
    row = next(x for x in after["rows"] if x["name"] == "Cambia Health")
    assert row["cadence"] == "monthly" and row["next_label"] == "Next refresh in 26 days"
    assert after["estimate_month"] < before["estimate_month"]
    off = (await _call(plane, "PUT", f"/v1/schedules/{cambia}", json={"cadence": "off"})).json()
    assert next(x for x in off["rows"] if x["name"] == "Cambia Health")["next_refresh_at"] is None
    await _call(plane, "PUT", f"/v1/schedules/{cambia}", json={"cadence": "2w"})


async def test_a_cadence_faster_than_the_client_allows_is_refused(world: dict[str, Any]) -> None:
    plane = _plane(world["b"], "other-test", world["payloads"])
    _sql("UPDATE client_settings SET fastest_schedule = 'weekly' WHERE tenant_id = %s", world["b"])
    try:
        view = (await _call(plane, "GET", "/v1/schedules")).json()
        options = {o["cadence"]: o["allowed"] for o in view["rows"][0]["options"]}
        assert options == {"3d": False, "weekly": True, "2w": True, "monthly": True, "off": True}
        cigna = _account(world["b"], "cigna.example")
        r = await _call(plane, "PUT", f"/v1/schedules/{cigna}", json={"cadence": "3d"})
        assert r.status_code == 422 and "weekly at most" in r.json()["detail"]
    finally:
        _sql("UPDATE client_settings SET fastest_schedule = '3d' WHERE tenant_id = %s", world["b"])


async def test_another_clients_account_cannot_be_scheduled(world: dict[str, Any]) -> None:
    plane = _plane(world["b"], "other-test", world["payloads"])
    theirs = _account(world["a"], "highmark.example")
    assert (
        await _call(plane, "PUT", f"/v1/schedules/{theirs}", json={"cadence": "off"})
    ).status_code == 404


async def test_a_due_refresh_starts_once_and_moves_the_next_date(world: dict[str, Any]) -> None:
    medica = _account(world["a"], "medica.example")
    now = datetime.now(UTC)
    _sql(
        "UPDATE accounts SET refresh_cadence = 'weekly', next_refresh_at = %s WHERE id = %s",
        now - timedelta(minutes=1),
        medica,
    )
    ticks = Recorder()
    await SC.tick(now, ticks)
    mine = [j for t, j in ticks.started if t == world["a"]]
    assert len(mine) == 1
    job = _sql(
        "SELECT kind, scope, status, requested_by FROM research_jobs WHERE id = %s", mine[0]
    )[0]
    assert job == ("scheduled_refresh", "full", "queued", "scheduler")
    nxt = _sql("SELECT next_refresh_at FROM accounts WHERE id = %s", medica)[0][0]
    assert nxt - now == timedelta(days=7)
    again = Recorder()
    await SC.tick(now + timedelta(minutes=5), again)
    assert not [j for t, j in again.started if t == world["a"] and j == mine[0]]
    _sql("UPDATE research_jobs SET status = 'canceled', finished_at = now() WHERE id = %s", mine[0])
    _sql("DELETE FROM credit_entries WHERE job_id = %s", mine[0])
    _sql(
        "UPDATE accounts SET refresh_cadence = 'off', next_refresh_at = NULL WHERE id = %s", medica
    )


async def test_a_due_refresh_with_no_research_left_is_skipped_and_the_owner_told(
    world: dict[str, Any],
) -> None:
    """A scheduled refresh is one of the owner's researches (owner decision 2026-10-14)."""
    tenant = world["b"]
    cigna = _account(tenant, "cigna.example")
    now = datetime.now(UTC)
    _sql(
        "INSERT INTO user_limits (tenant_id, owner_user_id, research_limit, updated_by)"
        " VALUES (%s, 'other-test', 0, 'test') ON CONFLICT (tenant_id, owner_user_id)"
        " DO UPDATE SET research_limit = 0",
        tenant,
    )
    _sql(
        "UPDATE accounts SET refresh_cadence = 'weekly', next_refresh_at = %s WHERE id = %s",
        now - timedelta(minutes=1),
        cigna,
    )
    try:
        first = Recorder()
        await SC.tick(now, first)
        assert not [j for t, j in first.started if t == tenant]
        assert not _sql(
            "SELECT 1 FROM research_jobs WHERE account_id = %s AND kind = 'scheduled_refresh'"
            " AND created_at >= %s",
            cigna,
            now - timedelta(minutes=1),
        )
        note = _sql(
            "SELECT kind, user_id FROM notifications WHERE account_id = %s"
            " AND kind = 'research_limit_reached'",
            cigna,
        )
        assert note == [("research_limit_reached", "other-test")]
        # the next refresh moved a cycle on, so the tick does not try again at once
        assert _sql("SELECT next_refresh_at > %s FROM accounts WHERE id = %s", now, cigna) == [
            (True,)
        ]
    finally:
        _sql(
            "DELETE FROM user_limits WHERE tenant_id = %s AND owner_user_id = 'other-test'", tenant
        )
        _sql(
            "UPDATE accounts SET refresh_cadence = 'off', next_refresh_at = NULL WHERE id = %s",
            cigna,
        )


async def test_the_schedule_lookups_return_identifiers_only(world: dict[str, Any]) -> None:
    with psycopg.connect(_admin()) as c:
        cur = c.execute("SELECT * FROM due_scheduled_refreshes(now())")
        assert [d.name for d in cur.description or []] == ["tenant_id", "account_id"]
        cur = c.execute("SELECT * FROM tenants_with_waiting_jobs()")
        assert [d.name for d in cur.description or []] == ["tenant_id"]
    assert CR.rates().estimates["full"].credits > 0
