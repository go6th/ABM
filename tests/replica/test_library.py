"""The 6th Library end to end on PostgreSQL (SYNTHETIC packages): the operator uploads finished
workspaces, the client-ready check finds the five files, leaves the internal-only file out and
refuses a package with a banned dash; only a passed account can be published, only to chosen
clients; a client adds it with no credits, counted toward the cap, and another client never sees
it.

GUARANTEE(test_a_library_account_is_checked_published_and_added_without_credits)
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import httpx
import psycopg
import pytest
from docx import Document
from openpyxl import Workbook

from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
OP = {"Authorization": "Bearer operator:op-library"}
NARRATIVE = (
    "Northwind Traders is consolidating its customer data platforms this year, and its new"
    " head of data started in March."
)


def _docx(*paragraphs: str) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _xlsx(*rows: list[str]) -> bytes:
    book = Workbook()
    ws = book.active
    assert ws is not None
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def package(summary: str = NARRATIVE) -> bytes:
    files = {
        "deliverables/Northwind_Account_Summary.docx": _docx("Northwind Traders", summary),
        "deliverables/Northwind_Sources_and_Evidence.docx": _docx("Sources and evidence"),
        "deliverables/Northwind_People_List.xlsx": _xlsx(["Name", "Role"], ["[Name]", "CIO"]),
        "deliverables/Northwind_Job_Postings.xlsx": _xlsx(["Role"], ["Data engineer"]),
        "deliverables/Northwind_Tech_Stack_and_Teams.xlsx": _xlsx(["Technology"], ["Snowflake"]),
        "Northwind_Internal_Only.docx": _docx("Call notes that name the account team."),
        "MANIFEST.json": b'{"account": "Northwind Traders"}',
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return out.getvalue()


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def _staff(plane: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp(
        now=plane.app.state.container.clock.now()
    )
    transport = httpx.ASGITransport(app=plane.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.test") as c:
        return await c.request(method, path, headers=OP, **kw)


async def test_a_library_account_is_checked_published_and_added_without_credits(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in (
        ("STAFF_JWT_ISSUER", "https://staff.example/"),
        ("STAFF_JWT_AUDIENCE", "staff"),
        ("STAFF_JWKS_URL", "https://staff.example/jwks"),
    ):
        monkeypatch.setenv(k, v)
    priya = _plane(world["a"], "priya-test", world["payloads"])
    other = _plane(world["b"], "other-test", world["payloads"])
    priya.app.state.container.payloads = other.app.state.container.payloads

    bad = await _staff(
        priya,
        "POST",
        "/operator/v1/library/uploads?filename=bad.zip&domain=badco.example",
        content=package("Northwind — a dash used as punctuation, which the check refuses."),
    )
    assert bad.status_code == 201, bad.text
    assert bad.json()["check_passed"] is False and "summary:banned_dashes" in bad.json()["issues"]
    refused = await _staff(
        priya,
        "POST",
        f"/operator/v1/library/{bad.json()['id']}/publish",
        json={"tenant_ids": [str(world["a"])]},
    )
    assert refused.status_code == 409

    good = await _staff(
        priya,
        "POST",
        "/operator/v1/library/uploads?filename=northwind.zip&domain=northwind.example",
        content=package(),
    )
    up = good.json()
    assert (up["deliverables_found"], up["internal_left_out"], up["check_passed"]) == (
        5,
        True,
        True,
    )
    stored = _db("SELECT files::text FROM library_accounts WHERE id = %s", up["id"])[0][0]
    assert "internal" not in stored.lower()  # the internal-only file was never stored
    pub = await _staff(
        priya,
        "POST",
        f"/operator/v1/library/{up['id']}/publish",
        json={"tenant_ids": [str(world["a"])]},
    )
    assert pub.json() == {"published": 1}
    listing = (await _staff(priya, "GET", "/operator/v1/library")).json()["accounts"]
    assert {a["id"]: a["clients"] for a in listing}[up["id"]] == 1

    offered = (await _call(priya, "GET", "/v1/library/available")).json()
    assert [(a["name"], a["in_workspace"]) for a in offered["accounts"]] == [
        ("Northwind Traders", False)
    ]
    assert (await _call(other, "GET", "/v1/library/available")).json()["accounts"] == []
    sneak = await _call(
        other, "POST", "/v1/accounts/from-library", json={"library_account_ids": [up["id"]]}
    )
    assert sneak.status_code == 404

    added = await _call(
        priya, "POST", "/v1/accounts/from-library", json={"library_account_ids": [up["id"]]}
    )
    assert added.status_code == 201, added.text
    assert added.json() == {"added": ["northwind.example"], "already_in_workspace": 0}
    account = _db(
        "SELECT origin, added_by, name FROM accounts WHERE tenant_id = %s AND domain ="
        " 'northwind.example'",
        world["a"],
    )
    assert account == [("library", "priya-test", "Northwind Traders")]
    assert _db(
        "SELECT kind, credits_estimated, credits_used, status FROM research_jobs j JOIN accounts a"
        " ON a.id = j.account_id WHERE a.domain = 'northwind.example' AND a.tenant_id = %s",
        world["a"],
    ) == [("library_add", 0, 0, "done")]
    assert _db(
        "SELECT count(*) FROM credit_entries e JOIN research_jobs j ON j.id = e.job_id"
        " WHERE j.kind = 'library_add' AND j.tenant_id = %s",
        world["a"],
    ) == [(0,)]
    kinds = {
        k
        for (k,) in _db(
            "SELECT d.kind FROM deliverables d JOIN accounts a ON a.id = d.account_id"
            " WHERE a.domain = 'northwind.example' AND a.tenant_id = %s",
            world["a"],
        )
    }
    assert kinds == {"summary", "evidence", "people", "jobs", "tech"}
    ws = (await _call(priya, "GET", "/v1/accounts/northwind.example/workspace")).json()
    assert ws["narrative"]["sentences"][0]["text"].startswith("Northwind Traders is")
    again = await _call(
        priya, "POST", "/v1/accounts/from-library", json={"library_account_ids": [up["id"]]}
    )
    assert again.json() == {"added": [], "already_in_workspace": 1}
    one = await _call(priya, "GET", "/v1/accounts/northwind.example/deliverables/download?ids=all")
    assert one.status_code == 200 and one.headers["content-type"] == "application/zip"
