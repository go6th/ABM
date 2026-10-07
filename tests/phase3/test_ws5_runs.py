"""WS5 client app backend (VW5, TN5): progress shows stage counts only, polling with ETag, the
bounded server-sent event stream, and a run started from the client app takes the tenant's
settings (lanes, exclusions, geography) and records the settings version. SYNTHETIC only.

Lying scenarios: progress that would carry a provider name, a credit count, a cost or an ETA; a
stream that never ends; a run for an excluded company; a run that forgets its settings version.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.safety.client_output import assert_client_safe
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, new_tenant, uid
from views import progress

STAGES = [("setup", "Setup"), ("pass1", "Pass 1"), ("baseline", "Baseline"), ("final", "Final")]
OWNERS = {"r1": "pass1", "r2": "pass1", "r3": "baseline", "r4": "final"}


def test_progress_shows_stage_counts_only() -> None:
    p = progress.compute("run", "RUNNING", STAGES, OWNERS, [("r1", "done"), ("r2", None)])
    # setup owns no rows: it is done once a later stage has settled one
    assert (p.stages_done, p.stages_total, p.text, p.current_stage) == (1, 4, "1 of 4", "Pass 1")
    p = progress.compute("run", "QUEUED", STAGES, OWNERS, [("r1", None)])
    assert (p.text, p.current_stage, p.label) == ("0 of 4", "Setup", "Queued")
    p = progress.compute(
        "run", "RUNNING", STAGES, OWNERS, [("r1", "done"), ("r2", "blocked"), ("r3", None)]
    )
    assert (p.text, p.current_stage, p.label) == ("2 of 4", "Baseline", "Researching")
    done = progress.compute("run", "PARTIAL", STAGES, OWNERS, [("r1", None)])
    assert (done.text, done.finished, done.current_stage, done.label) == (
        "4 of 4",
        True,
        None,
        "Complete with gaps",
    )
    body = json.dumps(done.as_json())
    assert_client_safe([body])
    for word in ("eta", "credit", "cost", "usd", "provider", "p_"):
        assert word not in body.lower().replace("stages_", ""), word
    assert (
        done.etag != p.etag
        and done.etag == progress.compute("run", "PARTIAL", STAGES, OWNERS, [("r1", None)]).etag
    )


async def _admin(plane: Plane, browser: Browser, name: str) -> tuple[UUID, str]:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    assert (
        await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    ).status_code == 303
    return t, slug


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_client_run_takes_the_tenant_settings(plane: Plane, browser: Browser) -> None:
    t, _ = await _admin(plane, browser, "ws5-run")
    settings_body = {
        "expected_version": 0,
        "settings": {
            "platform_lanes": ["sap", "salesforce"],
            "people_scope": {"levels_in_scope": ["VP", "Director"], "regions": ["India"]},
            "exclusions": {
                "companies": ["partner.example"],
                "people": ["https://profiles.example/in/ann"],
            },
            "pursuing_client": "Synthetic Client Co",
        },
    }
    saved = await browser.call("PUT", "/v1/settings", json=settings_body)
    assert saved.status_code == 200, saved.text
    refused = await browser.call(
        "POST",
        "/v1/runs",
        json={"domain": "eu.partner.example"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert refused.status_code == 409 and "excluded_company" in refused.text
    started = await browser.call(
        "POST",
        "/v1/runs",
        json={"domain": "acme-ws5.example", "pursuing_client": True},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    with psycopg.connect(ADMIN_DSN) as c:
        run_input, version = c.execute(
            "SELECT run_input, settings_version FROM account_runs WHERE id = %s", (run,)
        ).fetchone()
    assert version == saved.json()["version"]
    assert "sap" not in run_input["lanes_off"] and "servicenow" in run_input["lanes_off"]
    assert run_input["excluded_people"] == ["url:profiles.example/in/ann"]
    assert run_input["excluded_companies"] == ["partner.example"]
    assert run_input["people_regions"] == ["India"]
    assert run_input["pursuing_client"] == "Synthetic Client Co"
    accounts = (await browser.client.get("/v1/accounts")).json()["accounts"]
    assert [a["domain"] for a in accounts] == ["acme-ws5.example"]
    assert (
        await browser.call("POST", "/v1/accounts", json={"domain": "partner.example"})
    ).status_code == 409


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_polling_with_etag_and_a_bounded_event_stream(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    from account_intelligence.api.routes import runs

    t, _ = await _admin(plane, browser, "ws5-progress")
    started = await browser.call(
        "POST",
        "/v1/runs",
        json={"domain": "acme-sse.example"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    run = started.json()["run_id"]
    first = await browser.client.get(f"/v1/runs/{run}/progress")
    assert first.status_code == 200 and first.json()["stages_total"] >= 5
    again = await browser.client.get(
        f"/v1/runs/{run}/progress", headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304
    assert (await browser.client.get(f"/v1/runs/{uuid4()}/progress")).status_code == 404
    monkeypatch.setattr(
        runs,
        "progress_config",
        lambda: {"sse_max_duration_s": 0.3, "sse_heartbeat_s": 0.05, "sse_poll_s": 0.05},
    )
    stream = await browser.client.get(f"/v1/runs/{run}/events")
    text = stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert text.count("event: progress") == 1 and "event: reconnect" in text
    assert ": heartbeat" in text
    resumed = await browser.client.get(
        f"/v1/runs/{run}/events", headers={"Last-Event-ID": first.headers["etag"]}
    )
    assert "event: progress" not in resumed.text  # nothing changed since the last event
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE account_runs SET status = 'CANCELED', finished_at = now() WHERE id = %s", (run,)
        )
    final = await browser.client.get(f"/v1/runs/{run}/events")
    payload: Any = json.loads(final.text.split("data: ", 1)[1].split("\n", 1)[0])
    assert payload["finished"] and payload["label"] == "Cancelled"
    assert "reconnect" not in final.text  # a finished run ends the stream
