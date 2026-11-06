"""The "proposal, unmeasured" security values, measured against the code that enforces them
(fix prompt D, task 5; ADR-0126).

``config/session.yaml``, ``config/rate_limits.yaml``, ``config/auth.yaml`` and the MFA step-up
list in ``config/roles.yaml`` say ``status: proposal``. These tests pin the LIVE values and prove
the code enforces each one, not just that the YAML holds a number:

* the customer session (cookie ``__Host-ai_session``) ends 30 minutes after the last request and
  8 hours after sign-in, whatever the activity (``identity.sessions.load``), and "keep me signed
  in" gives the cookie exactly the absolute lifetime;
* the routes that need a fresh MFA sign-in, counted from the route catalogue (25);
* every per-user bucket in ``rate_limits.yaml`` and the sign-in account bucket in ``auth.yaml``
  refuse the first request past their capacity, in the shared Redis the cloud uses, with a
  Retry-After of one refill; the per-IP sign-in bucket is enforced at the edge (Cloud Armor) in
  the cloud, and that rule carries the same numbers.

A value the code does not enforce is a failing test, marked ``xfail(strict=True)`` with the
reason so the gate stays green and the gap stays visible.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
import yaml

from account_intelligence.config import CONFIG_DIR
from identity import sessions

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


# --- sessions ----------------------------------------------------------------------------------


class _Sessions:
    """Just enough of an AsyncSession for ``identity.sessions`` (one auth_sessions row)."""

    def __init__(self) -> None:
        self.row: dict[str, Any] = {}

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql, p = str(statement), params or {}
        if sql.startswith("INSERT INTO auth_sessions"):
            self.row = {
                "id": uuid.uuid4(),
                "tenant_id": uuid.uuid4(),
                "user_id": p["u"],
                "amr": p["amr"],
                "created_at": p["now"],
                "last_seen_at": p["now"],
                "absolute_expires_at": p["abs"],
                "idle_timeout_s": p["idle"],
                "step_up_at": p["step"],
                "revoked_at": None,
            }
            return SimpleNamespace(scalar_one=lambda: self.row["id"])
        if sql.startswith("SELECT id, tenant_id"):
            return SimpleNamespace(first=lambda: SimpleNamespace(**self.row) if self.row else None)
        if sql.startswith("UPDATE auth_sessions SET last_seen_at"):
            self.row["last_seen_at"] = p["now"]
            return None
        if sql.startswith("UPDATE auth_sessions SET revoked_at"):
            hit = self.row["revoked_at"] is None
            self.row["revoked_at"] = p["now"]
            return SimpleNamespace(
                first=lambda: SimpleNamespace(user_id=self.row["user_id"]) if hit else None
            )
        return None  # auth_events


async def _signed_in() -> tuple[_Sessions, str, sessions.SessionPolicy]:
    policy = sessions.load_policy()
    s = _Sessions()
    token, _ = await sessions.create(s, user_id="u-1", now=T0, policy=policy)  # type: ignore[arg-type]
    return s, token, policy


def test_the_live_session_policy_values() -> None:
    policy = sessions.load_policy()
    assert policy.session_cookie == "__Host-ai_session"
    assert policy.idle_timeout_s == 30 * 60
    assert policy.absolute_timeout_s == 8 * 60 * 60
    assert policy.step_up_window_s == 10 * 60


@pytest.mark.asyncio
async def test_a_session_ends_30_minutes_after_the_last_request() -> None:
    s, token, policy = await _signed_in()
    assert await sessions.load(s, token, T0 + timedelta(minutes=29, seconds=59), policy)  # type: ignore[arg-type]
    last = T0 + timedelta(minutes=29, seconds=59)
    assert await sessions.load(s, token, last + timedelta(minutes=29), policy)  # type: ignore[arg-type]
    seen = last + timedelta(minutes=29)
    assert await sessions.load(s, token, seen + timedelta(minutes=30), policy) is None  # type: ignore[arg-type]
    # an ended session never comes back
    assert await sessions.load(s, token, seen + timedelta(minutes=31), policy) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_session_ends_8_hours_after_sign_in_whatever_the_activity() -> None:
    s, token, policy = await _signed_in()
    now = T0
    # a request every 20 minutes keeps it from idling out ...
    while now + timedelta(minutes=20) < T0 + timedelta(hours=8):
        now += timedelta(minutes=20)
        assert await sessions.load(s, token, now, policy), now  # type: ignore[arg-type]
    assert await sessions.load(s, token, T0 + timedelta(hours=8) - timedelta(seconds=1), policy)  # type: ignore[arg-type]
    # ... until the absolute limit
    assert await sessions.load(s, token, T0 + timedelta(hours=8), policy) is None  # type: ignore[arg-type]


def test_keep_me_signed_in_gives_the_cookie_the_absolute_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.responses import Response

    from account_intelligence.api.routes import auth as auth_routes

    plane = SimpleNamespace(policy=sessions.load_policy())
    monkeypatch.setattr(auth_routes, "plane_of", lambda request: plane)
    for remember, max_age in ((True, "Max-Age=28800"), (False, None)):
        response = Response()
        auth_routes._cookie(response, "tok", SimpleNamespace(), remember=remember)
        cookie = next(
            v.decode()
            for k, v in response.raw_headers
            if k == b"set-cookie" and v.startswith(b"__Host-ai_session=")
        )
        assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/" in cookie
        assert "samesite=lax" in cookie.lower() and "Domain" not in cookie
        assert (max_age in cookie) if max_age else ("Max-Age" not in cookie)


# --- fresh MFA (step-up) -----------------------------------------------------------------------

# measured from the route catalogue on 2026-11-03 (the audit PDF says 27; 25 since the provider-key
# routes were parked, ADR-0136)
STEP_UP_ROUTES = {
    ("GET", "/v1/audit/export"),
    ("PATCH", "/v1/users"),
    ("POST", "/v1/connectors"),
    ("POST", "/v1/connectors/{connection_id}/disconnect"),
    ("POST", "/v1/connectors/{connection_id}/grants"),
    ("POST", "/v1/connectors/{connection_id}/quarantine/{item_id}/{decision}"),
    ("POST", "/v1/crm/{crm}/enable"),
    ("POST", "/v1/crm/{crm}/mappings"),
    ("POST", "/v1/crm/{crm}/preview"),
    ("POST", "/v1/crm/{crm}/revoke"),
    ("POST", "/v1/crm/{crm}/writes"),
    ("POST", "/v1/crm/{crm}/writes/approve"),
    ("POST", "/v1/runs/{run_id}/cap/approve"),
    ("POST", "/v1/runs/{run_id}/cap/extend"),
    ("POST", "/v1/runs/{run_id}/cap/reject"),
    ("POST", "/v1/settings/branding/logo"),
    ("POST", "/v1/settings/people-consent"),
    ("POST", "/v1/sso/connections"),
    ("POST", "/v1/support/grants"),
    ("POST", "/v1/users"),
    ("POST", "/v1/users/invite"),
    ("PUT", "/v1/documents/policy"),
    ("PUT", "/v1/settings"),
    ("PUT", "/v1/settings/branding"),
    ("PUT", "/v1/sso/connections/{connection_id}"),
}


def _step_up_catalogue() -> dict[tuple[str, str], str]:
    import account_intelligence.api.main  # noqa: F401  registers every route
    from account_intelligence.api.policy import ROUTE_POLICIES
    from identity.roles import needs_step_up

    return {
        key: p.action
        for key, p in ROUTE_POLICIES.items()
        if p.action is not None and needs_step_up(p.action)
    }


def test_the_routes_that_need_a_fresh_mfa_sign_in() -> None:
    got = _step_up_catalogue()
    assert set(got) == STEP_UP_ROUTES
    assert len(got) == 25


@pytest.mark.asyncio
async def test_a_step_up_route_refuses_an_mfa_sign_in_older_than_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard itself (not only the helper) refuses a stale step-up on a catalogue route."""
    from account_intelligence.api import guard
    from account_intelligence.api.errors import APIProblem
    from account_intelligence.api.policy import ROUTE_POLICIES

    policy = ROUTE_POLICIES[("PUT", "/v1/settings")]
    plane = SimpleNamespace(policy=sessions.load_policy(), clock=SimpleNamespace(now=lambda: T0))
    monkeypatch.setattr(guard, "plane_of", lambda request: plane)

    async def member(*a: Any) -> None:
        return None

    monkeypatch.setattr(guard, "check_membership", member)
    request = SimpleNamespace(method="GET", headers={}, state=SimpleNamespace(route_policy=policy))
    for minutes, refused in ((9, False), (10, True), (60, True)):
        ident = SimpleNamespace(
            kind="session",  # a GET: no CSRF token needed, the step-up check still applies
            role="admin",
            user_id="u-1",
            tenant_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            step_up_at=T0 - timedelta(minutes=minutes),
        )
        if refused:
            with pytest.raises(APIProblem) as err:
                await guard.authorize(request, ident)  # type: ignore[arg-type]
            assert err.value.type == "step_up_required"
        else:
            try:
                await guard.authorize(request, ident)  # type: ignore[arg-type]
            except Exception as error:  # noqa: BLE001 - only the step-up refusal is in scope
                assert getattr(error, "type", None) != "step_up_required"


@pytest.mark.parametrize(
    "action",
    [
        pytest.param(
            a,
            marks=pytest.mark.xfail(
                strict=True,
                reason="roles.yaml lists request_deletion for step-up, but no tenant route uses"
                " it: tenant deletion is requested on the operator plane only, so nothing"
                " enforces this entry",
            ),
        )
        if a == "request_deletion"
        else a
        for a in sorted(
            yaml.safe_load((CONFIG_DIR / "roles.yaml").read_text()).get("step_up")
            or ["approve_cap"]
        )
    ],
)
def test_every_step_up_action_guards_at_least_one_route(action: str) -> None:
    assert action in set(_step_up_catalogue().values())


# --- rate limits -------------------------------------------------------------------------------

USER_BUCKETS = {  # (capacity, refill per second) as live on 2026-11-03
    "document_upload": (120, 0.5),
    "drive_list": (60, 0.5),
    "part_retry": (60, 0.2),
    "research_resume": (20, 0.05),
    "download_everything": (10, 0.0028),
    "export": (30, 0.05),
    "archive_read": (120, 2),
    "maildrive_sync_google": (1, 0.0084),
    "maildrive_sync_microsoft": (1, 0.0084),
}


def test_the_live_bucket_values() -> None:
    from identity import rate_limit

    rate_limit.user_limits.cache_clear()
    assert rate_limit.user_limits() == {
        k: (float(c), float(r)) for k, (c, r) in USER_BUCKETS.items()
    }
    rate_limit._limits.cache_clear()
    assert rate_limit._limits() == {"ip": (30.0, 0.5), "account": (10.0, 0.1)}


@pytest_asyncio.fixture
async def shared_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The rate limiter on a real Redis (the local stack's, database 15), as in the cloud."""
    redis = pytest.importorskip("redis.asyncio")
    from identity import rate_limit

    url = os.getenv("RATE_LIMIT_TEST_REDIS", "redis://localhost:6379/15")
    client = redis.from_url(url)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("no Redis reachable (the local stack's redis on :6379)")
    monkeypatch.setattr(rate_limit, "get_settings", lambda: SimpleNamespace(redis_url=url))
    monkeypatch.setattr(rate_limit, "_redis_client", client)
    yield client
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "bucket"), sorted(USER_BUCKETS.items()))
async def test_each_user_bucket_refuses_past_its_capacity_in_redis(
    shared_redis: Any, action: str, bucket: tuple[float, float]
) -> None:
    from account_intelligence.api.errors import APIProblem
    from identity import rate_limit

    capacity, refill = bucket
    tenant, user = uuid.uuid4(), f"u-{uuid.uuid4().hex}"
    for _ in range(int(capacity)):
        await rate_limit.consume_user(tenant, user, action)
    with pytest.raises(APIProblem) as refused:
        await rate_limit.consume_user(tenant, user, action)
    assert refused.value.status == 429
    retry = int(refused.value.headers["Retry-After"])
    assert retry == math.ceil(1 / refill) or retry == math.ceil(1 / refill) - 1
    # another user's bucket is untouched
    await rate_limit.consume_user(tenant, f"u-{uuid.uuid4().hex}", action)


@pytest.mark.asyncio
async def test_the_sign_in_account_bucket_refuses_past_its_capacity_in_redis(
    shared_redis: Any,
) -> None:
    from account_intelligence.api.errors import APIProblem
    from identity import rate_limit

    request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.7"))
    account = f"{uuid.uuid4().hex}@example.com"
    for _ in range(10):
        await rate_limit.consume(request, account=account, action="login")
    with pytest.raises(APIProblem) as refused:
        await rate_limit.consume(request, account=account, action="login")
    assert refused.value.status == 429


def test_the_per_ip_sign_in_bucket_is_the_edge_rule_in_the_cloud() -> None:
    """With Redis configured the api skips its own per-IP bucket (its TCP peer is the web
    server); Cloud Armor's rule is the per-IP limit, and it must carry auth.yaml's numbers:
    30 at once, refilled at 0.5 a second, is 30 a minute."""
    edge = (ROOT / "infra/modules/edge/main.tf").read_text()
    rule = edge.split('description = "Stricter per-client limit')[1].split("rule {")[0]
    count = int(re.search(r"count\s*=\s*(\d+)", rule).group(1))  # type: ignore[union-attr]
    interval = int(re.search(r"interval_sec\s*=\s*(\d+)", rule).group(1))  # type: ignore[union-attr]
    for route in ("auth/(?:login", "sso/(?:discover"):
        assert route in rule
    assert (count, interval) == (30, 60) == (30, int(30 / 0.5))
