"""Mail and drive, operable end to end (SYNTHETIC; offline fakes for the provider): the callback
turns a provider error into a fixed code without a 422 and uses up the consent; the page lists
each source with its last sync outcome; "Sync now" syncs the caller's own connections only and is
rate limited; and a user's mail and drive is purged (revoked, token destroyed, items deleted,
audited) when an operator ends their access, when idle expiry ends it, when the client's feature
is turned off, and when the scheduled sync finds either.

GUARANTEE(test_sync_now_is_the_callers_own_and_rate_limited)
GUARANTEE(test_mail_and_drive_is_purged_when_access_or_the_feature_ends)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from connectors_client.maildrive import Item
from connectors_client.ports import TokenSet
from connectors_client.scopes import spec
from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
APPS = {"google": ("client-id-1", "client-secret-1")}
OP = {"Authorization": "Bearer operator:op-console"}


class FakeOAuth:
    """The provider's token endpoint: exchange, refresh and revoke, recording revocations."""

    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.issued = 0
        self.grant_scopes: tuple[str, ...] | None = None  # None: exactly the table's scopes

    async def exchange(
        self, kind: str, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        self.issued += 1
        scopes = spec(kind).scopes if self.grant_scopes is None else self.grant_scopes
        return TokenSet("access", f"refresh-{self.issued}", tuple(scopes), 3600)

    async def refresh(self, kind: str, refresh_token: str) -> TokenSet:
        return TokenSet("access", refresh_token, tuple(spec(kind).scopes), 3600)

    async def revoke(self, kind: str, token: str) -> None:
        self.revoked.append(token)


class FakeReader:
    """Recent items: one thread per watched domain, plus one that matches nobody."""

    def __init__(self) -> None:
        self.domains: list[str] = []
        self.calls = 0

    async def recent(self, kind: str, access_token: str, since: datetime) -> list[Item]:
        self.calls += 1
        out = [
            Item(f"t-{d}", "thread", "Pilot scope", "Next steps", None, (f"ann@{d}",))
            for d in self.domains
        ]
        return [*out, Item("t-x", "thread", "Lunch", "Friday?", None, ("bob@unrelated.example",))]


def _wire(plane: Any, fake: FakeOAuth, reader: FakeReader) -> None:
    extras = plane.app.state.container.extras
    extras["maildrive_apps"] = APPS
    extras["maildrive_oauth"] = fake
    extras["maildrive_reader"] = reader


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _user(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:6]}"


def _account(tenant: UUID, user: str) -> str:
    domain = f"{user}.example"
    _db(
        "INSERT INTO accounts (tenant_id, domain, added_by, owner_user_id) VALUES"
        " (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        tenant,
        domain,
        user,
        user,
    )
    return domain


async def _connect(plane: Any, kind: str = "gmail") -> str:
    started = await _call(plane, "POST", f"/v1/mail-and-drive/{kind}/connect")
    assert started.status_code == 200, started.text
    state = str(parse_qs(urlsplit(started.json()["authorize_url"]).query)["state"][0])
    done = await _call(
        plane, "GET", f"/v1/mail-and-drive/callback?state={state}&code=c1", follow_redirects=False
    )
    assert (done.status_code, done.headers["location"]) == (303, "/mail-and-drive")
    return state


def _conns(tenant: UUID, user: str) -> list[tuple[Any, ...]]:
    return _db(
        "SELECT id, status, token_ref FROM connector_connections WHERE tenant_id = %s"
        " AND owner_user_id = %s AND provider IS NOT NULL ORDER BY created_at",
        tenant,
        user,
    )


def _items(tenant: UUID, user: str) -> int:
    return int(
        _db(
            "SELECT count(*) FROM connector_items WHERE tenant_id = %s AND owner_user_id = %s",
            tenant,
            user,
        )[0][0]
    )


async def _staff(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    from tests.fakes.phase3 import FakeStaffIdp
    from tests.phase3.conftest import BASE

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


async def test_the_callback_turns_provider_errors_into_codes(world: dict[str, Any]) -> None:
    fake, reader = FakeOAuth(), FakeReader()
    plane = _plane(world["a"], _user("cb"), world["payloads"])
    _wire(plane, fake, reader)

    started = (await _call(plane, "POST", "/v1/mail-and-drive/gmail/connect")).json()
    state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
    declined = await _call(
        plane,
        "GET",
        f"/v1/mail-and-drive/callback?state={state}&error=access_denied"
        "&error_description=The+user+said+no+%3Cscript%3E",
        follow_redirects=False,
    )
    assert declined.status_code == 303
    assert declined.headers["location"] == "/mail-and-drive?error=denied"  # no provider text
    again = await _call(
        plane, "GET", f"/v1/mail-and-drive/callback?state={state}&code=c1", follow_redirects=False
    )
    assert again.headers["location"] == "/mail-and-drive?error=expired"  # the state is used up
    assert fake.issued == 0  # the declined consent never called the provider

    for query, code in (
        ("error=server_error", "provider"),
        ("error=temporarily_unavailable&state=short", "provider"),
        ("error=invalid_scope", "scope"),
        ("error=something_else", "consent"),
        ("", "consent"),  # neither a code nor an error: no 422
        (f"state={'s' * 40}&code=c1", "expired"),  # an unknown state
    ):
        reply = await _call(
            plane, "GET", f"/v1/mail-and-drive/callback?{query}", follow_redirects=False
        )
        assert (reply.status_code, reply.headers["location"]) == (
            303,
            f"/mail-and-drive?error={code}",
        ), query

    fake.grant_scopes = ("https://www.googleapis.com/auth/gmail.modify",)  # wider than read-only
    started = (await _call(plane, "POST", "/v1/mail-and-drive/gmail/connect")).json()
    state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
    wider = await _call(
        plane, "GET", f"/v1/mail-and-drive/callback?state={state}&code=c2", follow_redirects=False
    )
    assert wider.headers["location"] == "/mail-and-drive?error=scope"


async def test_the_page_lists_each_source_with_its_last_sync(world: dict[str, Any]) -> None:
    user = _user("page")
    plane = _plane(world["a"], user, world["payloads"])
    _wire(plane, FakeOAuth(), FakeReader())
    await _connect(plane, "gmail")
    [(conn, _, _)] = _conns(world["a"], user)
    _db(
        "UPDATE connector_connections SET last_sync_status = 'failed',"
        " last_error = 'provider_unavailable', last_error_at = now(), last_sync_listed = 3,"
        " last_sync_matched = 1 WHERE id = %s",
        conn,
    )
    page = (await _call(plane, "GET", "/v1/mail-and-drive")).json()
    by_kind = {s["kind"]: s for s in page["sources"]}
    assert list(by_kind) == ["gmail", "google_drive", "outlook", "onedrive", "sharepoint"]
    gmail = by_kind["gmail"]
    assert (gmail["family"], gmail["group"], gmail["status"]) == ("google", "mail", "connected")
    assert (gmail["last_sync_status"], gmail["last_error"]) == ("failed", "provider_unavailable")
    assert (gmail["last_sync_listed"], gmail["last_sync_matched"]) == (3, 1)
    assert gmail["last_error_at"] is not None and gmail["broken_reason"] is None
    assert by_kind["google_drive"]["status"] == "not_connected"
    assert by_kind["outlook"]["status"] == "not_available"  # no Microsoft app registered
    assert by_kind["outlook"]["family"] == "microsoft"
    assert page["mail"]["status"] == "connected"  # the older cards are kept
    assert page["mail"]["last_error"] == "provider_unavailable"

    _db(
        "UPDATE connector_connections SET status = 'broken', broken_reason = 'token_revoked'"
        " WHERE id = %s",
        conn,
    )
    gmail = (await _call(plane, "GET", "/v1/mail-and-drive")).json()["sources"][0]
    assert (gmail["status"], gmail["broken_reason"]) == ("broken", "token_revoked")


async def test_sync_now_is_the_callers_own_and_rate_limited(world: dict[str, Any]) -> None:
    tenant = world["a"]
    me, other = _user("sync-me"), _user("sync-other")
    fake, reader = FakeOAuth(), FakeReader()
    mine, theirs = _plane(tenant, me, world["payloads"]), _plane(tenant, other, world["payloads"])
    for plane in (mine, theirs):
        _wire(plane, fake, reader)
    # one secret store per test application: the colleague connects through the same one
    theirs.app.state.container.tenant_plane = mine.app.state.container.tenant_plane
    await _connect(mine, "gmail")
    await _connect(theirs, "gmail")
    reader.domains = [_account(tenant, me), _account(tenant, other)]

    assert (await _call(mine, "POST", "/v1/mail-and-drive/microsoft/sync")).status_code == 409
    done = await _call(mine, "POST", "/v1/mail-and-drive/google/sync")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["family"] == "google"
    [synced] = body["sources"]
    assert (synced["kind"], synced["state"], synced["listed"], synced["matched"]) == (
        "gmail",
        "ok",
        len(reader.domains) + 1,
        1,  # only the caller's own account matches
    )
    assert _items(tenant, me) == 1 and _items(tenant, other) == 0  # the colleague's untouched
    [(_, _, _)] = _conns(tenant, other)
    assert _db(
        "SELECT last_synced_at FROM connector_connections WHERE tenant_id = %s"
        " AND owner_user_id = %s",
        tenant,
        other,
    ) == [(None,)]
    audit = _db(
        "SELECT actor FROM audit_events WHERE tenant_id = %s"
        " AND action = 'admin.maildrive_sync_now'",
        tenant,
    )
    assert (me,) in audit

    again = await _call(mine, "POST", "/v1/mail-and-drive/google/sync")
    assert again.status_code == 429
    assert int(again.headers["Retry-After"]) >= 1
    assert (await _call(mine, "POST", "/v1/mail-and-drive/mars/sync")).status_code == 422

    page = (await _call(mine, "GET", "/v1/mail-and-drive")).json()["sources"][0]
    assert (page["last_sync_status"], page["last_sync_matched"]) == ("ok", 1)


async def test_mail_and_drive_is_purged_when_access_or_the_feature_ends(
    world: dict[str, Any], staff_env: None
) -> None:
    tenant = world["a"]
    user = _user("leaver")
    fake, reader = FakeOAuth(), FakeReader()
    plane = _plane(tenant, user, world["payloads"])
    _wire(plane, fake, reader)
    await _connect(plane, "gmail")
    reader.domains = [_account(tenant, user)]
    assert (await _call(plane, "POST", "/v1/mail-and-drive/google/sync")).status_code == 200
    assert _items(tenant, user) == 1
    [(conn, _, ref)] = _conns(tenant, user)

    ended = await _staff(
        plane, "POST", f"/operator/v1/console/users/{tenant}/terminate", json={"user_id": user}
    )
    assert ended.status_code == 200, ended.text
    assert _conns(tenant, user) == [(conn, "disconnected", None)]
    assert _items(tenant, user) == 0
    assert fake.revoked == ["refresh-1"]  # revoked at the source
    assert ref in plane.secrets.destroyed  # the stored token is gone
    assert _db(
        "SELECT actor_kind, outcome FROM audit_events WHERE tenant_id = %s"
        " AND action = 'admin.maildrive_purged' AND resource_id = %s",
        tenant,
        str(conn),
    ) == [("operator", "ok")]


async def test_turning_the_feature_off_purges_every_users_connections(
    world: dict[str, Any], staff_env: None
) -> None:
    tenant = world["b"]
    fake, reader = FakeOAuth(), FakeReader()
    users = [_user("off-a"), _user("off-b")]
    planes = [_plane(tenant, u, world["payloads"]) for u in users]
    for plane in planes:
        _wire(plane, fake, reader)
        plane.app.state.container.tenant_plane = planes[0].app.state.container.tenant_plane
    for plane in planes:
        await _connect(plane, "gmail")
    reader.domains = [_account(tenant, u) for u in users]
    for plane in planes:
        assert (await _call(plane, "POST", "/v1/mail-and-drive/google/sync")).status_code == 200
    assert [_items(tenant, u) for u in users] == [1, 1]
    # the admin's client-wide connector (no provider) is never part of the purge
    admin_conn = uuid4()
    _db(
        "INSERT INTO connector_connections (id, tenant_id, kind, status, owner_user_id,"
        " created_by, created_at) VALUES (%s, %s, 'google_drive', 'active', 'admin-test',"
        " 'admin-test', now())",
        admin_conn,
        tenant,
    )
    try:
        off = await _staff(
            planes[0],
            "PUT",
            f"/operator/v1/console/clients/{tenant}",
            json={"mail_drive_allowed": False},
        )
        assert off.status_code == 200, off.text
        for u in users:
            assert [status for _, status, _ in _conns(tenant, u)] == ["disconnected"]
            assert _items(tenant, u) == 0
        assert sorted(fake.revoked) == ["refresh-1", "refresh-2"]
        assert _db("SELECT status FROM connector_connections WHERE id = %s", admin_conn) == [
            ("active",)
        ]
        refused = await _call(planes[0], "POST", "/v1/mail-and-drive/google/sync")
        assert refused.status_code == 403
    finally:
        _db("DELETE FROM connector_connections WHERE id = %s", admin_conn)
        _db("UPDATE client_settings SET mail_drive_allowed = true WHERE tenant_id = %s", tenant)


async def test_the_scheduled_sync_and_idle_expiry_purge_instead_of_syncing(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from account_intelligence.db.session import set_system, set_user_scope, tenant_txn
    from account_intelligence.replica import access
    from connectors_client import lifecycle

    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    tenant = world["b"]
    fake, reader = FakeOAuth(), FakeReader()
    gone, idle = _user("gone"), _user("idle")
    planes = {u: _plane(tenant, u, world["payloads"]) for u in (gone, idle)}
    for plane in planes.values():
        _wire(plane, fake, reader)
        plane.app.state.container.tenant_plane = planes[gone].app.state.container.tenant_plane
    for plane in planes.values():
        await _connect(plane, "gmail")
    [(gone_conn, _, _)] = _conns(tenant, gone)

    # the owner's access ended without the purge (e.g. before this release): the tick purges
    _db(
        "UPDATE memberships SET access_state = 'terminated', ended_at = now()"
        " WHERE tenant_id = %s AND user_id = %s",
        tenant,
        gone,
    )
    tick = await _call(planes[gone], "POST", "/tasks/maildrive-sync", json={})
    assert tick.status_code == 200, tick.text
    assert tick.json()["purged"] >= 1 and "deferred" in tick.json()
    assert _conns(tenant, gone) == [(gone_conn, "disconnected", None)]
    assert [s for _, s, _ in _conns(tenant, idle)] == ["active"]  # synced, not purged

    # idle expiry ends access and purges in the same transaction
    _db(
        "UPDATE memberships SET last_active_at = now() - interval '400 days'"
        " WHERE tenant_id = %s AND user_id = %s",
        tenant,
        idle,
    )
    store = planes[idle].app.state.container.tenant_plane.secrets

    async def purge(s: Any, user_id: str, now: datetime) -> object:
        return await lifecycle.purge_owner(
            s, owner_user_id=user_id, oauth=fake, store=store, now=now, reason="access_expired"
        )

    marker = set_system()
    try:
        async with tenant_txn(tenant) as s:
            _, expired = await access.expire_idle(s, datetime.now(UTC), purge)
    finally:
        set_user_scope()
        del marker
    assert expired >= 1
    assert [s for _, s, _ in _conns(tenant, idle)] == ["disconnected"]

    # a client with the feature off: the tick purges instead of syncing
    owner = _user("off-tick")
    plane = _plane(tenant, owner, world["payloads"])
    _wire(plane, fake, reader)
    plane.app.state.container.tenant_plane = planes[gone].app.state.container.tenant_plane
    await _connect(plane, "gmail")
    _db("UPDATE client_settings SET mail_drive_allowed = false WHERE tenant_id = %s", tenant)
    try:
        tick = await _call(plane, "POST", "/tasks/maildrive-sync", json={})
        assert tick.status_code == 200, tick.text
        assert tick.json()["purged"] >= 1
        assert [s for _, s, _ in _conns(tenant, owner)] == ["disconnected"]
    finally:
        _db("UPDATE client_settings SET mail_drive_allowed = true WHERE tenant_id = %s", tenant)
