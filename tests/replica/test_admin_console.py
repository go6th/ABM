"""The admin console on PostgreSQL (SYNTHETIC clients and users): Add client returns an invite
link (nothing is sent) that the invited admin opens to choose a password and sign in; clients,
overview, users and runs are counts only and never carry question text; a client's limits and
features change from its page; Terminate ends a user's access on every path and Re-enable
restores it; the platform defaults and the Model split change for new clients and new runs.

GUARANTEE(test_add_client_invites_an_admin_who_can_sign_in)
GUARANTEE(test_terminated_access_is_refused_everywhere_and_re_enabled)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
import pytest

from llm.config import load_routing_config, with_model_split
from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import BASE, Browser, make_app
from tests.replica.conftest import _call, _plane

OP = {"Authorization": "Bearer operator:op-console"}
SECRET_QUESTION = "Which canary-question-text should never reach the console?"


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def _staff(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(
        now=plane.app.state.container.clock.now()
    )
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        return await c.request(method, path, headers=OP, **kw)


@pytest.fixture
def staff_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in (
        ("STAFF_JWT_ISSUER", "https://staff.example/"),
        ("STAFF_JWT_AUDIENCE", "staff"),
        ("STAFF_JWKS_URL", "https://staff.example/jwks"),
        ("STAFF_OPERATOR_EMAILS", "admin@6th.example"),
        ("STAFF_SUPPORT_EMAILS", "dev@6th.example"),
    ):
        monkeypatch.setenv(k, v)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_add_client_invites_an_admin_who_can_sign_in(staff_env: None) -> None:
    plane = make_app()
    email = f"lead-{uuid4().hex[:8]}@harbor.example"
    made = await _staff(
        plane,
        "POST",
        "/operator/v1/console/clients",
        json={"name": "Harbor Digital", "admin_email": email},
    )
    assert made.status_code == 201, made.text
    invite = made.json()
    url = urlsplit(invite["invite_url"])
    assert url.path == "/invite" and url.fragment and url.fragment not in url.path
    tenant = invite["tenant_id"]
    row = next(
        c
        for c in (await _staff(plane, "GET", "/operator/v1/console/clients")).json()["clients"]
        if c["tenant_id"] == tenant
    )
    assert (row["name"], row["status"], row["users"]) == ("Harbor Digital", "Invited", 1)
    detail = (await _staff(plane, "GET", f"/operator/v1/console/clients/{tenant}")).json()
    assert detail["pending_invites"] == [email]

    password = "Long-unique-violet-credential-5521"
    browser = Browser(plane)
    try:
        before = await browser.client.post(
            "/v1/auth/login", json={"email": email, "password": password}, headers={"Origin": BASE}
        )
        assert before.status_code == 401  # nothing works until the invite is accepted
        accepted = await browser.client.post(
            "/v1/auth/invite/accept",
            json={"token": url.fragment, "password": password},
            headers={"Origin": BASE},
        )
        assert accepted.status_code == 200, accepted.text
        again = await browser.client.post(
            "/v1/auth/invite/accept",
            json={"token": url.fragment, "password": password},
            headers={"Origin": BASE},
        )
        assert again.status_code == 400  # single use
        login = await browser.client.post(
            "/v1/auth/login", json={"email": email, "password": password}, headers={"Origin": BASE}
        )
        assert login.status_code == 200, login.text
    finally:
        await browser.close()
    detail = (await _staff(plane, "GET", f"/operator/v1/console/clients/{tenant}")).json()
    assert detail["pending_invites"] == []
    renew = await _staff(plane, "POST", f"/operator/v1/console/clients/{tenant}/invite")
    assert renew.status_code == 409


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_console_shows_counts_and_never_question_text(
    world: dict[str, Any], staff_env: None
) -> None:
    priya = _plane(world["a"], "priya-test", world["payloads"])
    asked = await _call(
        priya,
        "POST",
        "/v1/chat/ask",
        json={"request_id": str(uuid4()), "question": SECRET_QUESTION},
    )
    assert asked.status_code == 200
    plane = make_app()
    bodies = []
    for path in ("overview", "clients", "users", "runs", f"clients/{world['a']}"):
        r = await _staff(plane, "GET", f"/operator/v1/console/{path}")
        assert r.status_code == 200, (path, r.text)
        bodies.append(r.text)
        assert "canary-question-text" not in r.text
    overview = __import__("json").loads(bodies[0])
    assert overview["questions"] >= 1 and len(overview["weekly"]) == 5
    assert all(c["label"] for c in overview["categories"])
    one = await _staff(plane, "GET", f"/operator/v1/console/overview?client={world['a']}")
    assert one.json()["weekly_client"] == str(world["a"])
    users = __import__("json").loads(bodies[2])["users"]
    mine = next(
        u for u in users if u["user_id"] == "priya-test" and u["tenant_id"] == str(world["a"])
    )
    assert mine["questions"] >= 1 and mine["access"] == "active" and mine["account_cap"] == 34


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_client_settings_change_from_its_page(world: dict[str, Any], staff_env: None) -> None:
    plane = make_app()
    path = f"/operator/v1/console/clients/{world['b']}"
    changed = await _staff(
        plane,
        "PUT",
        path,
        json={"account_cap_per_user": 12, "idle_expiry_days": 14, "mail_drive_allowed": False},
    )
    assert changed.status_code == 200, changed.text
    s = changed.json()["settings"]
    assert (s["account_cap_per_user"], s["idle_expiry_days"], s["mail_drive_allowed"]) == (
        12,
        14,
        False,
    )
    bad = await _staff(plane, "PUT", path, json={"idle_expiry_days": 9})
    assert bad.status_code == 422
    unknown = await _staff(plane, "PUT", path, json={"library_account_ids": [str(uuid4())]})
    assert unknown.status_code == 409
    other = await _staff(plane, "GET", f"/operator/v1/console/clients/{uuid4()}")
    assert other.status_code == 404


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_terminated_access_is_refused_everywhere_and_re_enabled(
    world: dict[str, Any], staff_env: None
) -> None:
    from identity.membership import active_role

    user = f"temp-{uuid4().hex[:6]}"
    member = _plane(world["a"], user, world["payloads"])
    assert (await _call(member, "GET", "/v1/me")).status_code == 200
    plane = make_app()
    ended = await _staff(
        plane,
        "POST",
        f"/operator/v1/console/users/{world['a']}/terminate",
        json={"user_id": user},
    )
    assert ended.json() == {"changed": True, "access": "terminated"}
    from account_intelligence.db.session import tenant_txn

    async with tenant_txn(world["a"]) as s:
        assert await active_role(s, user) is None  # every sign-in method and API call reads this
    fresh = _plane(world["a"], user, world["payloads"])  # a new process: no cached decision
    assert (await _call(fresh, "GET", "/v1/me")).status_code == 401
    events = _db(
        "SELECT event, reason FROM membership_events WHERE tenant_id = %s AND user_id = %s"
        " ORDER BY at",
        world["a"],
        user,
    )
    assert events[-1] == ("suspended", "terminated")
    back = await _staff(
        plane,
        "POST",
        f"/operator/v1/console/users/{world['a']}/re-enable",
        json={"user_id": user},
    )
    assert back.json() == {"changed": True, "access": "active"}
    again = _plane(world["a"], user, world["payloads"])
    assert (await _call(again, "GET", "/v1/me")).status_code == 200


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_platform_defaults_and_the_model_choice(staff_env: None) -> None:
    from llm.choice import catalogue

    opus, fable, kimi = catalogue().ids
    plane = make_app()
    before = (await _staff(plane, "GET", "/operator/v1/console/defaults")).json()
    assert before["admins"] == [
        {"email": "admin@6th.example", "role": "Operator"},
        {"email": "dev@6th.example", "role": "Support"},
    ]
    assert before["skill_version"] and before["model_router_on"] is False
    assert (before["default_research_model"], before["default_analysis_model"]) == (opus, fable)
    assert [m["id"] for m in before["models"]] == [opus, fable, kimi]
    assert [m["needs_client_permission"] for m in before["models"]] == [False, False, True]
    try:
        r = await _staff(
            plane,
            "PUT",
            "/operator/v1/console/defaults",
            json={"default_analysis_model": opus, "credit_cap_per_pull": 350},
        )
        assert r.status_code == 200, r.text
        assert (r.json()["default_analysis_model"], r.json()["credit_cap_per_pull"]) == (opus, 350)
        for bad in (
            {"default_research_model": "unknown"},
            {"enabled_models": [opus], "default_analysis_model": fable},  # default not enabled
        ):
            got = await _staff(plane, "PUT", "/operator/v1/console/defaults", json=bad)
            assert got.status_code == 422, (bad, got.text)
    finally:
        _db(
            "UPDATE platform_defaults SET enabled_models = NULL, default_research_model = NULL,"
            " default_analysis_model = NULL, credit_cap_per_pull = %s WHERE id = 1",
            before["credit_cap_per_pull"],
        )


def test_single_model_moves_extraction_to_the_analysis_tier_and_keeps_the_judge() -> None:
    cfg = load_routing_config()
    single = with_model_split(cfg, "single")
    assert with_model_split(cfg, "split") is cfg
    for name, kind in cfg.task_kinds.items():
        moved = single.task_kinds[name].tier
        assert moved == ("planner" if kind.tier == "extractor" else kind.tier)
    assert single.routing_version != cfg.routing_version  # no reply reuse across modes
    with pytest.raises(ValueError):
        with_model_split(cfg, "mixed")
