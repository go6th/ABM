"""Mail and drive per user (SYNTHETIC): the real OAuth and read clients run against a fake Google
endpoint. Connect is bound to the user who started it; the sync keeps only items that match the
workspace's accounts and only for their owner; Disconnect revokes, destroys the token and deletes
what was synced; without an OAuth app a source is "Not available yet"; a client with the feature
off cannot connect.

GUARANTEE(test_mail_and_drive_connects_syncs_and_disconnects_per_user)
GUARANTEE(test_mail_and_drive_keeps_only_matched_items_for_their_owner)
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest

from connectors_client.maildrive import HttpMailDriveReader, HttpOAuthClient, Item, matches
from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class FakeGoogle:
    """The token, revoke and Gmail endpoints, recording what they were asked."""

    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "scope": SCOPE,
                    "expires_in": 3600,
                },
            )
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        assert request.headers["authorization"] == "Bearer access-1"
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if request.url.path.endswith("/messages/m1"):
            heads = [
                {"name": "Subject", "value": "Pilot scope"},
                {"name": "From", "value": "Ann <ann@highmark.example>"},
                {"name": "Date", "value": "Mon, 12 Oct 2026 09:00:00 +0000"},
            ]
            return httpx.Response(
                200, json={"threadId": "t1", "snippet": "Next steps", "payload": {"headers": heads}}
            )
        heads = [
            {"name": "Subject", "value": "Lunch"},
            {"name": "From", "value": "bob@unrelated.example"},
        ]
        return httpx.Response(
            200, json={"threadId": "t2", "snippet": "Friday?", "payload": {"headers": heads}}
        )


def _wire(plane: Any, google: FakeGoogle, apps: dict[str, tuple[str, str]]) -> None:
    extras = plane.app.state.container.extras
    extras["maildrive_apps"] = apps
    extras["maildrive_oauth"] = HttpOAuthClient(apps, transport=google.transport)
    extras["maildrive_reader"] = HttpMailDriveReader(transport=google.transport)


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_mail_and_drive_connects_syncs_and_disconnects_per_user(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    google = FakeGoogle()
    apps = {"google": ("client-id-1", "client-secret-1")}
    maya = _plane(world["a"], "maya-test", world["payloads"])
    priya = _plane(world["a"], "priya-test", world["payloads"])
    for plane in (maya, priya):
        _wire(plane, google, apps)

    page = (await _call(maya, "GET", "/v1/mail-and-drive")).json()
    assert page["allowed"] is True
    assert page["mail"]["status"] == "not_connected"
    available = {o["kind"]: o["available"] for o in page["drive"]["options"]}
    assert available == {"google_drive": True, "onedrive": False, "sharepoint": False}

    started = (await _call(maya, "POST", "/v1/mail-and-drive/gmail/connect")).json()
    query = parse_qs(urlsplit(started["authorize_url"]).query)
    assert query["client_id"] == ["client-id-1"] and query["scope"] == [SCOPE]
    state = query["state"][0]
    stolen = await _call(
        priya, "GET", f"/v1/mail-and-drive/callback?state={state}&code=c1", follow_redirects=False
    )
    assert stolen.headers["location"] == "/mail-and-drive?error=consent"  # bound to maya
    done = await _call(
        maya, "GET", f"/v1/mail-and-drive/callback?state={state}&code=c1", follow_redirects=False
    )
    assert done.status_code == 303 and done.headers["location"] == "/mail-and-drive"

    synced = await _call(maya, "POST", "/tasks/maildrive-sync", json={})
    assert synced.status_code == 200, synced.text
    rows = _db(
        "SELECT owner_user_id, title, location FROM connector_items WHERE owner_user_id ="
        " 'maya-test' AND tenant_id = %s",
        world["a"],
    )
    assert rows == [("maya-test", "Pilot scope", "Gmail")]  # the unrelated thread is not kept
    mine = (await _call(maya, "GET", "/v1/mail-and-drive")).json()["mail"]
    assert (mine["status"], mine["accounts"], mine["items"], mine["source"]) == (
        "connected",
        1,
        1,
        "Gmail",
    )
    theirs = (
        await _call(priya, "GET", "/v1/accounts/highmark.example/sheets/mail-and-drive")
    ).json()
    assert "Pilot scope" not in json.dumps(theirs)  # never shown to another user

    gone = (await _call(maya, "POST", "/v1/mail-and-drive/mail/disconnect")).json()
    assert gone == {"disconnected": 1, "items_deleted": 1}
    assert google.revoked == ["refresh-1"]
    assert _db(
        "SELECT status, token_ref FROM connector_connections WHERE owner_user_id = 'maya-test'"
        " AND tenant_id = %s",
        world["a"],
    ) == [("disconnected", None)]
    assert (await _call(maya, "GET", "/v1/mail-and-drive")).json()["mail"]["status"] == (
        "not_connected"
    )


async def test_without_an_app_or_with_the_feature_off_nothing_connects(
    world: dict[str, Any],
) -> None:
    plane = _plane(world["b"], "nomail-test", world["payloads"])
    _wire(plane, FakeGoogle(), {})
    page = (await _call(plane, "GET", "/v1/mail-and-drive")).json()
    assert (page["mail"]["status"], page["drive"]["status"]) == ("not_available", "not_available")
    assert (await _call(plane, "POST", "/v1/mail-and-drive/gmail/connect")).status_code == 409
    _db("UPDATE client_settings SET mail_drive_allowed = false WHERE tenant_id = %s", world["b"])
    _wire(plane, FakeGoogle(), {"google": ("i", "s")})
    off = await _call(plane, "POST", "/v1/mail-and-drive/gmail/connect")
    assert off.status_code == 403
    assert (await _call(plane, "GET", "/v1/mail-and-drive")).json()["allowed"] is False


async def test_mail_and_drive_keeps_only_matched_items_for_their_owner() -> None:
    from uuid import uuid4

    from connectors_client.maildrive import Account

    acme, beta = uuid4(), uuid4()
    accounts = [Account(acme, "acme.example", "Acme"), Account(beta, "beta.example", "Beta Corp")]
    by_mail = Item("r1", "thread", "Hello", "", None, ("jo@eu.acme.example",))
    by_name = Item("r2", "file", "Beta Corp renewal plan.docx", "", None)
    neither = Item("r3", "thread", "Alphabet soup", "betamax", None, ("x@other.example",))
    assert matches(by_mail, accounts) == [acme]
    assert matches(by_name, accounts) == [beta]
    assert matches(neither, accounts) == []


async def test_one_broken_connection_never_stops_the_sync_of_the_others(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
    from uuid import uuid4

    bad = uuid4()
    _db(
        "INSERT INTO connector_connections (id, tenant_id, kind, provider, status,"
        " owner_user_id, scopes, created_by, created_at) VALUES (%s, %s, 'gmail', 'google',"
        " 'active', 'tokenless-test', ARRAY[%s], 'tokenless-test', now())",
        bad,
        world["b"],
        SCOPE,
    )
    plane = _plane(world["b"], "tokenless-test", world["payloads"])
    _wire(plane, FakeGoogle(), {})  # no OAuth app: not available, never marked broken
    tick = await _call(plane, "POST", "/tasks/maildrive-sync", json={})
    assert tick.status_code == 200, tick.text
    assert _db("SELECT status FROM connector_connections WHERE id = %s", bad) == [("active",)]
    _wire(plane, FakeGoogle(), {"google": ("i", "s")})
    tick = await _call(plane, "POST", "/tasks/maildrive-sync", json={})
    assert tick.status_code == 200, tick.text  # the tick finishes for everyone else
    assert _db("SELECT status, broken_reason FROM connector_connections WHERE id = %s", bad) == [
        ("broken", "token_missing")
    ]
