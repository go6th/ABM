"""A deleted account disappears from every listing and lookup (merge review 2026-10-30).

The delete route marks the account (``accounts.deleted_at``); each reader of the account list,
its pages, its archive, its exports, its runs, the schedules, the notifications and the Ask
route must hide it, and adding the same domain again brings it back with its history.
SYNTHETIC only.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, new_tenant, uid

DOMAIN = "removed-reader.example"
KEPT = "kept-reader.example"


async def _admin(plane: Plane, browser: Browser, name: str) -> Any:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    assert (
        await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    ).status_code == 303
    return t


def _sql(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN) as c:
        cur = c.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        c.commit()
    return rows


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_deleted_account_is_hidden_from_every_reader_and_comes_back_on_add(
    plane: Plane, browser: Browser
) -> None:
    t = await _admin(plane, browser, "deleted-readers")
    for d in (DOMAIN, KEPT):
        assert (await browser.call("POST", "/v1/accounts", json={"domain": d})).status_code == 201
    started = await browser.call(
        "POST", "/v1/runs", json={"domain": DOMAIN}, headers={"Idempotency-Key": uuid4().hex}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    _sql("UPDATE account_runs SET status = 'CANCELED', finished_at = now() WHERE id = %s", run_id)
    account_id = _sql("SELECT id FROM accounts WHERE tenant_id = %s AND domain = %s", t, DOMAIN)[0][
        0
    ]
    _sql(
        "INSERT INTO notifications (tenant_id, user_id, kind, account_id) SELECT %s, owner_user_id,"
        " 'research_finished', id FROM accounts WHERE id = %s",
        t,
        account_id,
    )
    # while it is listed, every reader shows it
    assert any(r["domain"] == DOMAIN for r in (await browser.client.get("/v1/runs")).json()["runs"])
    assert any(
        s["domain"] == DOMAIN for s in (await browser.client.get("/v1/schedules")).json()["rows"]
    )
    assert (await browser.client.get(f"/v1/runs/{run_id}")).status_code == 200
    assert any(
        n.get("account_domain") == DOMAIN
        for n in (await browser.client.get("/v1/notifications")).json()["notifications"]
    )

    gone = await browser.call("DELETE", f"/v1/accounts/{DOMAIN}", json={"confirm": DOMAIN})
    assert gone.status_code == 200, gone.text

    get = browser.client.get
    base = f"/v1/accounts/{DOMAIN}"
    for path in (
        base,
        f"{base}/workspace",
        f"{base}/tables/people",
        f"{base}/sheets/people",
        f"{base}/archive/list",
        f"{base}/archive?url=https%3A%2F%2F{DOMAIN}%2Fcareers",
        f"{base}/parity/versions",
        f"{base}/refresh",
        f"/v1/runs/{run_id}",
    ):
        assert (await get(path)).status_code == 404, path
    assert [a["domain"] for a in (await get("/v1/accounts")).json()["accounts"]] == [KEPT]
    assert all(r["domain"] != DOMAIN for r in (await get("/v1/runs")).json()["runs"])
    assert all(s["domain"] != DOMAIN for s in (await get("/v1/schedules")).json()["rows"])
    assert all(
        n.get("account_domain") != DOMAIN
        for n in (await get("/v1/notifications")).json()["notifications"]
    )
    assert (await get(f"/v1/exports?domain={DOMAIN}")).status_code in (200, 404)
    if (await get(f"/v1/exports?domain={DOMAIN}")).status_code == 200:
        assert (await get(f"/v1/exports?domain={DOMAIN}")).json()["packages"] == []
    sched = await browser.call("PUT", f"/v1/schedules/{account_id}", json={"cadence": "monthly"})
    assert sched.status_code == 404
    ask = await browser.call(
        "POST",
        "/v1/chat/ask",
        json={
            "request_id": str(uuid4()),
            "question": "What changed?",
            "account_id": str(account_id),
        },
    )
    assert ask.status_code in (404, 422), ask.text
    assert (await get(f"/v1/documents?account_id={account_id}")).json()["documents"] == []
    assert _sql("SELECT deleted_at IS NOT NULL FROM accounts WHERE id = %s", account_id) == [
        (True,)
    ]

    # adding the same domain again brings the account and its history back
    back = await browser.call("POST", "/v1/accounts", json={"domain": DOMAIN})
    assert back.status_code == 201 and back.json()["created"] is True
    assert (await get(base)).status_code == 200
    assert sorted(a["domain"] for a in (await get("/v1/accounts")).json()["accounts"]) == sorted(
        [DOMAIN, KEPT]
    )
    assert any(r["domain"] == DOMAIN for r in (await get("/v1/runs")).json()["runs"])


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_queued_research_job_blocks_the_delete_like_a_running_research(
    plane: Plane, browser: Browser
) -> None:
    """A job that has not made its run yet would start on the account later and bring it back."""
    t = await _admin(plane, browser, "deleted-queued-job")
    domain = "queued-job.example"
    assert (await browser.call("POST", "/v1/accounts", json={"domain": domain})).status_code == 201
    account_id = _sql("SELECT id FROM accounts WHERE tenant_id = %s AND domain = %s", t, domain)[0][
        0
    ]
    _sql(
        "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
        " credits_estimated, requested_by) VALUES (%s, %s, 'new_research', 'full', 'queued',"
        " to_char(now(), 'YYYY-MM'), 1, 'u')",
        t,
        account_id,
    )
    busy = await browser.call("DELETE", f"/v1/accounts/{domain}", json={"confirm": domain})
    assert busy.status_code == 409 and busy.json()["type"].endswith("account_busy")
    _sql(
        "UPDATE research_jobs SET status = 'failed', finished_at = now() WHERE account_id = %s",
        account_id,
    )
    done = await browser.call("DELETE", f"/v1/accounts/{domain}", json={"confirm": domain})
    assert done.status_code == 200, done.text
