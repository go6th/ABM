"""The scheduled sweep and audit-verify task routes (audit 2026-10-15): the sweeper runs on a
schedule instead of only while the worker is warm, and every client's audit chain is checked
nightly."""

from __future__ import annotations

from typing import Any

import pytest

from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_the_scheduled_sweep_runs_one_pass(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    plane = _plane(world["a"], "priya-test", world["payloads"])
    swept = await _call(plane, "POST", "/tasks/sweep", json={})
    assert swept.status_code == 200, swept.text
    assert swept.json() == {"swept": True}


async def test_audit_verify_checks_every_clients_chain(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    plane = _plane(world["a"], "priya-test", world["payloads"])
    checked = await _call(plane, "POST", "/tasks/audit-verify", json={})
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["clients"] >= 2 and body["broken"] == 0  # world a and b at least, both intact


async def test_task_routes_refuse_without_the_scheduler_token(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    for path in ("/tasks/sweep", "/tasks/audit-verify", "/tasks/metrics"):
        refused = await _call(plane, "POST", path, json={})
        assert refused.status_code in (401, 403), path


async def test_a_failed_company_confirm_leaves_no_empty_account(world: dict[str, Any]) -> None:
    """Binding fails (an unknown document): the account made for it is removed again, not left
    empty in the user's list (audit 2026-10-15)."""
    from uuid import uuid4

    import psycopg

    from tests.conftest import ADMIN_DSN

    plane = _plane(world["a"], "priya-test", world["payloads"])
    domain = f"ghost-{uuid4().hex[:8]}.example"
    got = await _call(
        plane,
        "POST",
        "/v1/documents/confirm-company",
        json={"document_ids": [str(uuid4())], "company_name": "Ghost", "domain": domain},
    )
    assert got.status_code == 404, got.text
    with psycopg.connect(ADMIN_DSN) as c:
        left = c.execute("SELECT count(*) FROM accounts WHERE domain = %s", (domain,)).fetchone()
    assert left == (0,)


async def test_notifications_wait_without_a_mailer_and_are_delivered_with_one(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run's prompt becomes one outbox row; the relay leaves it waiting while no mailer is set
    up, then delivers it to the staff operators once one is (audit 2026-10-15)."""
    from datetime import UTC, datetime
    from uuid import uuid4

    import psycopg

    from account_intelligence.db.session import tenant_txn
    from spend.notify import OutboxNotifier
    from tests.conftest import ADMIN_DSN
    from tests.fakes.phase3 import FakeMailer

    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    monkeypatch.setenv("STAFF_OPERATOR_EMAILS", "ops@6th.example")
    with psycopg.connect(ADMIN_DSN) as c:
        run = c.execute(
            "SELECT r.id FROM account_runs r JOIN accounts a ON a.id = r.account_id"
            " WHERE a.tenant_id = %s LIMIT 1",
            (world["a"],),
        ).fetchone()[0]
    clock = type("C", (), {"now": staticmethod(lambda: datetime.now(UTC))})()
    notifier = OutboxNotifier(tenant_txn, world["a"], clock)
    kind = f"cap_approval_{uuid4().hex[:6]}"
    await notifier.prompt(str(run), kind, 2)
    await notifier.prompt(str(run), kind, 2)  # the same prompt again: one row (dedupe)
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT count(*) FROM notification_outbox WHERE dedupe_key = %s",
            (f"prompt:{run}:{kind}:2",),
        ).fetchone()
    assert rows == (1,)

    plane = _plane(world["a"], "priya-test", world["payloads"])
    container = plane.app.state.container
    from account_intelligence.api.tenant_plane import UnconfiguredMailer

    container.tenant_plane.mailer = UnconfiguredMailer()
    waiting = (await _call(plane, "POST", "/tasks/notify-relay", json={})).json()
    assert waiting == {"relayed": False, "reason": "no_mailer"}

    mailer = FakeMailer()
    container.tenant_plane.mailer = mailer
    sent = await _call(plane, "POST", "/tasks/notify-relay", json={})
    assert sent.status_code == 200, sent.text
    assert sent.json()["delivered"] >= 1
    mine = [m for m in mailer.sent if kind in m[2]["subject"]]
    assert mine and mine[0][0] == "ops@6th.example" and mine[0][1] == "notification"
    assert str(run) in mine[0][2]["body"]


async def test_the_metrics_task_counts_stuck_runs_without_recording_observations(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The every-two-minutes metrics task (audit 2026-10-15): writes the heartbeat and the
    stuck-run gauge for every state and age, and only counts (no observation rows)."""
    import psycopg

    from obs import setup
    from tests.conftest import ADMIN_DSN

    recorded: list[tuple[str, float, dict[str, Any]]] = []
    monkeypatch.setattr(setup, "record", lambda n, v, labels=None: recorded.append((n, v, labels)))
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    with psycopg.connect(ADMIN_DSN) as c:
        before = c.execute("SELECT count(*) FROM stuck_run_observations").fetchone()
    plane = _plane(world["a"], "priya-test", world["payloads"])
    got = await _call(plane, "POST", "/tasks/metrics", json={})
    assert got.status_code == 200, got.text
    assert got.json()["clients"] >= 2
    names = [n for n, _, _ in recorded]
    assert names.count("heartbeat") == 1
    gauges = {(lb["state"], lb["age_bucket"]) for n, _, lb in recorded if n == "stuck_runs"}
    assert len(gauges) == 12  # 3 states x 4 ages, zeros included so cleared runs reset
    with psycopg.connect(ADMIN_DSN) as c:
        after = c.execute("SELECT count(*) FROM stuck_run_observations").fetchone()
    assert after == before
