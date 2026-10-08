"""ABM run modes through the API (D-102, G14 to G16). SYNTHETIC only.

Lying scenarios: a free-text mode, a Phase 2 only run with no pull, with another tenant's pull,
and with a pull of another account; the same key reused for another mode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, new_tenant, uid

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _signed_in(plane: Plane, browser: Browser, name: str) -> UUID:
    t = new_tenant(name)
    slug = f"abm-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    assert (
        await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    ).status_code == 303
    return t


async def _start(browser: Browser, body: dict, key: str | None = None):  # type: ignore[no-untyped-def,type-arg]
    return await browser.call(
        "POST", "/v1/runs", json=body, headers={"Idempotency-Key": key or uuid4().hex}
    )


def _row(run: str) -> tuple:  # type: ignore[type-arg]
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT run_mode, parent_run_id, skill_config_version, skill_config_reason,"
            " tenant_id FROM account_runs WHERE id = %s",
            (run,),
        ).fetchone()
    assert row is not None
    return row


def _store_manifest(tenant: UUID, run: str, domain: str) -> None:
    rows = [
        {
            "row_key": "company_news",
            "status": "stored",
            "payload_refs": ["pl/1"],
            "call_refs": [],
            "queries": [],
            "cost_micros": 0,
            "pulled_at": "2026-10-08T09:00:00+00:00",
            "blocked_reason": None,
        }
    ]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO pull_manifests (tenant_id, run_id, account_key, config_version,"
            " digest, manifest, created_at) VALUES (%s, %s, %s, 'sk', %s, %s, %s)",
            (
                tenant,
                run,
                domain,
                "0" * 64,
                json.dumps({"rows": rows}),
                datetime(2026, 10, 8, tzinfo=UTC),
            ),
        )


async def test_no_mode_is_full_and_mode_is_recorded(plane: Plane, browser: Browser) -> None:
    await _signed_in(plane, browser, "abm-default")
    started = await _start(browser, {"domain": "default-abm.example"})
    assert started.status_code == 202, started.text
    mode, parent, version, reason, _ = _row(started.json()["run_id"])
    assert (mode, parent, reason) == ("full", None, "current") and version.startswith("sk-")


async def test_mode_is_a_typed_field_only(plane: Plane, browser: Browser) -> None:
    await _signed_in(plane, browser, "abm-typed")
    for bad in ("Phase 1 only", "PULL", "both"):
        assert (
            await _start(browser, {"domain": "typed-abm.example", "mode": bad})
        ).status_code == 422


async def test_phase_one_then_phase_two(plane: Plane, browser: Browser) -> None:
    t = await _signed_in(plane, browser, "abm-split")
    domain = "split-abm.example"
    option = await browser.client.get("/v1/runs/pull-option", params={"domain": domain})
    assert option.status_code == 200 and option.json() == {"available": False}
    pull = await _start(browser, {"domain": domain, "mode": "pull"})
    assert pull.status_code == 202 and _row(pull.json()["run_id"])[0] == "pull"
    no_data = await _start(
        browser, {"domain": domain, "mode": "build", "parent_run_id": pull.json()["run_id"]}
    )
    assert no_data.status_code == 422 and "no_pulled_data" in no_data.text  # no manifest yet
    _store_manifest(t, pull.json()["run_id"], domain)
    option = (await browser.client.get("/v1/runs/pull-option", params={"domain": domain})).json()
    assert option["available"] and option["rows_stored"] == 1 and option["pulled_at"]
    build = await _start(
        browser, {"domain": domain, "mode": "build", "parent_run_id": pull.json()["run_id"]}
    )
    assert build.status_code == 202, build.text
    mode, parent, _, reason, _ = _row(build.json()["run_id"])
    assert (mode, str(parent), reason) == ("build", pull.json()["run_id"], "inherited")
    other_account = await _start(
        browser,
        {
            "domain": "elsewhere-abm.example",
            "mode": "build",
            "parent_run_id": pull.json()["run_id"],
        },
    )
    assert other_account.status_code == 422 and "no_pulled_data" in other_account.text


async def test_build_without_or_with_a_foreign_pull_fails_fast(
    plane: Plane, browser: Browser
) -> None:
    await _signed_in(plane, browser, "abm-foreign")
    missing = await _start(browser, {"domain": "f-abm.example", "mode": "build"})
    assert missing.status_code == 422 and "no_pulled_data" in missing.text
    unknown = await _start(
        browser, {"domain": "f-abm.example", "mode": "build", "parent_run_id": str(uuid4())}
    )
    assert unknown.status_code == 422
    with psycopg.connect(ADMIN_DSN) as c:
        count = c.execute(
            "SELECT count(*) FROM account_runs a JOIN accounts x ON"
            " x.id = a.account_id WHERE x.domain = 'f-abm.example'"
        ).fetchone()
    assert count == (0,)  # nothing written


async def test_same_key_for_another_mode_is_a_conflict(plane: Plane, browser: Browser) -> None:
    await _signed_in(plane, browser, "abm-idem")
    key = uuid4().hex
    first = await _start(browser, {"domain": "idem-abm.example", "mode": "pull"}, key)
    again = await _start(browser, {"domain": "idem-abm.example", "mode": "pull"}, key)
    assert first.status_code == 202 and again.json()["run_id"] == first.json()["run_id"]
    other = await _start(browser, {"domain": "idem-abm.example"}, key)
    assert other.status_code == 409
