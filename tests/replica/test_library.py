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


def _docx(*paragraphs: str, link: str | None = None) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if link:  # an external hyperlink relationship, as Word writes one
        from docx.opc.constants import RELATIONSHIP_TYPE as RT

        doc.part.relate_to(link, RT.HYPERLINK, is_external=True)
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


def package(
    summary: str = NARRATIVE,
    *,
    manifest: bool = True,
    without: str | None = None,
    link: str | None = None,
) -> bytes:
    files = {
        "deliverables/Northwind_Account_Summary.docx": _docx(
            "Northwind Traders", summary, link=link
        ),
        "deliverables/Northwind_Sources_and_Evidence.docx": _docx("Sources and evidence"),
        "deliverables/Northwind_People_List.xlsx": _xlsx(["Name", "Role"], ["[Name]", "CIO"]),
        "deliverables/Northwind_Job_Postings.xlsx": _xlsx(["Role"], ["Data engineer"]),
        "deliverables/Northwind_Tech_Stack_and_Teams.xlsx": _xlsx(["Technology"], ["Snowflake"]),
        "Northwind_Internal_Only.docx": _docx("Call notes that name the account team."),
        "MANIFEST.json": b'{"account": "Northwind Traders"}',
    }
    if not manifest:
        del files["MANIFEST.json"]
    if without:
        files = {k: v for k, v in files.items() if without not in k}
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
        content=package(without="Job_Postings"),
    )
    assert bad.status_code == 201, bad.text
    assert bad.json()["check_passed"] is False and bad.json()["issues"] == ["jobs:missing"]
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


async def test_the_library_mends_links_and_dashes_and_names_the_account_from_its_files() -> None:
    """A workspace built outside the platform (no MANIFEST.json, a plain-http link in the Summary,
    an em dash in a quoted line) is named by its file names and made client ready: the stored
    Summary keeps every word with a plain hyphen, and its link target is https."""
    from account_intelligence.replica import library as L

    data = package(
        "Northwind — a dash in a quoted line.", manifest=False, link="http://northwind.example/news"
    )
    with pytest.raises(L.LibraryError):  # a lone file with no account in its name
        L.inspect_package("summary.docx", _docx("x"), None, "n.example")
    found = L.inspect_package("workspace.zip", data, None, "northwind.example")
    assert (found.name, found.issues, found.check_passed) == ("Northwind", [], True)
    stored = found.files["summary"][1]
    text = "\n".join(p.text for p in Document(io.BytesIO(stored)).paragraphs)
    assert "Northwind - a dash in a quoted line." in text and "—" not in text
    rels = [b for n, b in L._members("s.zip", stored) if n.endswith(".rels")]
    assert any(b"https://northwind.example/news" in b for b in rels)
    assert not any(b'Target="http://' in b for b in rels)
    # a file that needs no mending is stored byte for byte
    plain = _docx("Northwind Traders", NARRATIVE)
    assert L.client_ready(plain) == plain
