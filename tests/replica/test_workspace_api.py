"""Accounts, the Account workspace, deliverable downloads, /v1/me and Ask through the real
application on PostgreSQL, over the demo world the dev seed builds (SYNTHETIC: .example domains,
"[Name]" people). Two clients hold a Cigna account each; neither ever sees the other's."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

ROOT = Path(__file__).resolve().parents[2]


def _seed_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "seed_replica_demo", ROOT / "scripts" / "seed_replica_demo.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    import os

    from tests.conftest import ADMIN_DSN

    seed = _seed_module()
    payload_dir = tmp_path_factory.mktemp("payloads")
    os.environ["PAYLOAD_DIR"] = str(payload_dir)
    now = datetime.now(UTC)
    with psycopg.connect(ADMIN_DSN) as conn:
        s = seed.Seeder(conn, now)
        a = s.tenant(f"Replica A {uuid4().hex[:6]}")
        s.client_settings(a, pool=2000, cap=34)
        for builder in (seed.highmark, seed.cambia, seed.cigna, seed.medica):
            s.account(a, "priya-test", builder())
        b = s.tenant(f"Replica B {uuid4().hex[:6]}")
        s.client_settings(b, pool=3000, cap=10)
        s.account(b, "other-test", seed.cigna())
        conn.commit()
    return {"a": a, "b": b, "payloads": payload_dir}


def _plane(tenant: UUID, user: str, payload_dir: Path) -> Any:
    from account_intelligence.api.auth import Identity, identity
    from account_intelligence.platform.hermetic import LocalPayloadStore
    from tests.phase3.conftest import add_member, make_app

    add_member(tenant, user, "analyst")
    plane = make_app()
    plane.app.state.container.payloads = LocalPayloadStore(payload_dir)
    now = datetime.now(UTC)

    async def ident() -> Identity:
        return Identity(user, tenant, "analyst", now)

    plane.app.dependency_overrides[identity] = ident
    return plane


async def _call(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.test") as c:
        return await c.request(method, path, **kw)


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for raw in body.strip().split("\n\n"):
        lines = raw.split("\n")
        event = lines[0].removeprefix("event: ")
        out.append((event, json.loads(lines[1].removeprefix("data: "))))
    return out


async def test_accounts_rows_carry_the_designs_sub_line_states(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _call(plane, "GET", "/v1/accounts?limit=200")
    assert r.status_code == 200, r.text
    rows = {a["name"]: a for a in r.json()["accounts"]}
    assert (rows["Highmark"]["state"], rows["Highmark"]["new_signals"]) == ("new_signals", 2)
    assert (rows["Cambia Health"]["state"], rows["Cambia Health"]["new_signals"]) == (
        "new_signals",
        1,
    )
    assert rows["Cigna"]["state"] == "first_scan"
    assert rows["Medica"]["state"] == "no_change"
    assert rows["Highmark"]["lanes"] == ["Salesforce", "Data and AI"]
    assert rows["Highmark"]["cadence_label"] == "Weekly"
    assert rows["Highmark"]["last_researched_label"] == "2 days ago"


async def test_workspace_after_a_refresh_shows_what_changed_and_live_counts(
    world: dict[str, Any],
) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _call(plane, "GET", "/v1/accounts/highmark.example/workspace")
    assert r.status_code == 200, r.text
    ws = r.json()
    assert ws["first_scan"] is False and ws["changes"] is not None
    assert ws["subline"] == "Refreshed 2 days ago · Weekly schedule · Web: not used for answers"
    assert {row["grade"] for row in ws["changes"]["rows"]} <= {"confirmed", "probable"}
    assert ws["counts"] == {
        "signals": 9,
        "people": 41,
        "jobs": 27,
        "tech": 14,
        "news": 12,
        "mail_drive": 3,
        "sources": 86,
    }
    assert [f["name"] for f in ws["files"]] == [
        "Account summary",
        "People list",
        "Job postings",
        "Tech stack and teams",
        "Evidence",
    ]
    assert all(s["source_url"] for s in ws["narrative"]["sentences"])


async def test_workspace_on_a_first_scan_has_findings_and_no_changes(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    ws = (await _call(plane, "GET", "/v1/accounts/cigna.example/workspace")).json()
    assert ws["first_scan"] is True and ws["changes"] is None
    assert [f["grade"] for f in ws["findings"]] == ["confirmed", "probable", "hypothesis"]
    assert ws["subline"].startswith("First scan · ")


async def test_mail_and_drive_count_is_the_callers_own(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "someone-else", world["payloads"])
    ws = (await _call(plane, "GET", "/v1/accounts/highmark.example/workspace")).json()
    assert ws["counts"]["mail_drive"] == 0


async def test_one_file_downloads_as_itself_and_several_as_a_zip(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    files = (await _call(plane, "GET", "/v1/accounts/highmark.example/deliverables")).json()[
        "files"
    ]
    one = await _call(
        plane, "GET", f"/v1/accounts/highmark.example/deliverables/download?ids={files[0]['id']}"
    )
    assert one.status_code == 200
    assert one.headers["content-disposition"] == f'attachment; filename="{files[0]["filename"]}"'
    assert one.content[:2] == b"PK"
    ids = ",".join(f["id"] for f in files[:3])
    several = await _call(
        plane, "GET", f"/v1/accounts/highmark.example/deliverables/download?ids={ids}"
    )
    names = zipfile.ZipFile(io.BytesIO(several.content)).namelist()
    assert sorted(names) == sorted(f["filename"] for f in files[:3])
    everything = await _call(
        plane, "GET", "/v1/accounts/highmark.example/deliverables/download?ids=all"
    )
    assert len(zipfile.ZipFile(io.BytesIO(everything.content)).namelist()) == 5


async def test_another_clients_account_and_files_are_not_found(world: dict[str, Any]) -> None:
    a = _plane(world["a"], "priya-test", world["payloads"])
    b = _plane(world["b"], "other-test", world["payloads"])
    files = (await _call(a, "GET", "/v1/accounts/highmark.example/deliverables")).json()["files"]
    r = await _call(b, "GET", "/v1/accounts/highmark.example/workspace")
    assert r.status_code == 404
    r = await _call(
        b, "GET", f"/v1/accounts/cigna.example/deliverables/download?ids={files[0]['id']}"
    )
    assert r.status_code == 404
    own = (await _call(b, "GET", "/v1/accounts/cigna.example/workspace")).json()
    theirs = (await _call(a, "GET", "/v1/accounts/cigna.example/workspace")).json()
    assert own["id"] != theirs["id"]


async def test_me_counts_the_users_own_accounts_against_the_cap(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    me = (await _call(plane, "GET", "/v1/me")).json()
    assert (me["accounts_used"], me["account_cap"], me["accounts_total"]) == (4, 34, 4)
    other = _plane(world["a"], "nobody-test", world["payloads"])
    assert (await _call(other, "GET", "/v1/me")).json()["accounts_used"] == 0


async def test_ask_streams_a_cited_answer_and_keeps_it(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    request_id = str(uuid4())
    body = {"request_id": request_id, "question": "Who owns ServiceNow at Cigna?"}
    r = await _call(plane, "POST", "/v1/chat/ask", json=body)
    frames = dict(_frames(r.text))
    assert "error" not in frames, frames
    answer, thread = frames["answer"], frames["thread"]
    assert answer["answered"] and answer["sources"] and answer["used"]["web"] == "not_used"
    assert answer["offer"]["account_domain"] == "cigna.example"
    got = (await _call(plane, "GET", f"/v1/chat/threads/{thread['thread_id']}")).json()
    assert got["turns"][0]["answer"] == answer and got["title"] == "Who owns ServiceNow at Cigna?"
    again = dict(
        _frames(
            (
                await _call(
                    plane, "POST", "/v1/chat/ask", json={**body, "thread_id": thread["thread_id"]}
                )
            ).text
        )
    )
    assert again["done"]["replayed"] is True and again["answer"] == answer
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN) as c:
        n = c.execute(
            "SELECT sum(questions) FROM question_categories WHERE tenant_id = %s AND category ="
            " 'people'",
            (world["a"],),
        ).fetchone()
    assert n is not None and n[0] >= 1


async def test_ask_refuses_another_clients_account_and_thread(world: dict[str, Any]) -> None:
    a = _plane(world["a"], "priya-test", world["payloads"])
    b = _plane(world["b"], "other-test", world["payloads"])
    first = dict(
        _frames(
            (
                await _call(
                    a,
                    "POST",
                    "/v1/chat/ask",
                    json={"request_id": str(uuid4()), "question": "What changed this week?"},
                )
            ).text
        )
    )
    r = await _call(
        b,
        "POST",
        "/v1/chat/ask",
        json={
            "request_id": str(uuid4()),
            "question": "x",
            "thread_id": first["thread"]["thread_id"],
        },
    )
    assert r.status_code == 404
    ws_a = (await _call(a, "GET", "/v1/accounts/highmark.example/workspace")).json()
    r = await _call(
        b,
        "POST",
        "/v1/chat/ask",
        json={"request_id": str(uuid4()), "question": "x", "account_id": ws_a["id"]},
    )
    assert r.status_code == 404


async def test_suggestions_come_from_the_workspace(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    got = (await _call(plane, "GET", "/v1/chat/suggestions")).json()
    assert got["accounts"] == 4
    assert "Who owns ServiceNow at Cigna?" in got["suggestions"]
