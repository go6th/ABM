"""The parity service and routes on the database: a rendered version is recorded and stored,
served to the tenant's readers with the internal-only material for admins only, paged per
table, downloaded with the manifest's digest, refused for another tenant, and corrected into
the next version. Needs Postgres, LibreOffice, Node and the built Word renderer."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.api.auth import Identity, identity
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from parity.recalc import soffice_binary
from parity.render import DOCX_CLI
from parity.service import ParityService, ParityVersions
from tests.phase3.conftest import add_member, make_app
from tests.phase3.test_ws4_views import _get, _post
from tests.tracker.world import make_world

pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        soffice_binary() is None or shutil.which("node") is None or not DOCX_CLI.exists(),
        reason="LibreOffice, Node or the Word renderer is missing",
    ),
]


def plane_for(
    tenant: UUID, store: MemoryPayloadStore, role: str = "admin", user: str = "test-owner"
) -> Any:
    add_member(tenant, user, role)
    plane = make_app()
    plane.app.state.container.payloads = store
    now = datetime.now(UTC)

    async def ident() -> Identity:
        return Identity(user, tenant, role, now)

    plane.app.dependency_overrides[identity] = ident
    return plane


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def test_a_version_is_rendered_served_and_corrected(synthetic_doc: dict[str, Any]) -> None:
    w = make_world()
    store = MemoryPayloadStore()
    service = ParityService(tenant_txn, store, _Clock())
    found = await service.render_version(w.tenant, w.account, w.run, synthetic_doc)
    assert found.version == 1 and found.status == "built"
    assert found.bundle_key.startswith(
        f"tenants/{w.tenant}/accounts/{w.account}/runs/{w.run}/parity/v1/"
    )
    names = {f["name"] for f in found.manifest["files"]}
    assert "Acme_People_List.xlsx" in names and "Acme_Internal_Only.docx" in names
    async with tenant_txn(w.tenant) as s:
        again = await ParityVersions(store).get(s, w.account)
    assert again is not None and again.version == 1

    base = f"/v1/accounts/{w.domain}/parity"
    admin = plane_for(w.tenant, store)
    r = await _get(admin, f"{base}/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["bundle"]["meta"]["account_short"] == "Acme"
    assert "internal_only" in body["bundle"]["prose"]
    r = await _get(admin, f"{base}/manifest", params={"version": 1})
    assert r.status_code == 200
    manifest = r.json()
    assert any(f["name"] == "Acme_Internal_Only.docx" for f in manifest["files"])
    assert manifest["summary"]
    r = await _get(admin, f"{base}/versions")
    assert [v["version"] for v in r.json()["versions"]] == [1]

    analyst = plane_for(w.tenant, store, "analyst", "analyst-1")
    r = await _get(analyst, f"{base}/bundle")
    assert r.status_code == 404  # another user of the client: the rows are owner-scoped

    viewer_owner = plane_for(w.tenant, store, "viewer")
    r = await _get(viewer_owner, f"{base}/bundle")
    assert r.status_code == 200
    assert "internal_only" not in r.json()["bundle"]["prose"]
    assert "pursuing_client_internal" not in r.json()["bundle"]["meta"]
    r = await _get(viewer_owner, f"{base}/manifest")
    assert all("Internal_Only" not in f["name"] for f in r.json()["files"])
    r = await _get(viewer_owner, f"{base}/files/Acme_Internal_Only.docx")
    assert r.status_code == 404
    r = await _get(viewer_owner, f"{base}/screen")
    assert r.status_code == 200
    screen = r.json()
    assert [t["id"] for t in screen["tables"]][:2] == ["summary_legend", "summary_news"]
    assert screen["download_all"] and screen["download_all"].endswith("_workspace.zip?version=1")
    assert all(f["file"] != "internal" for f in screen["files"])
    assert len(screen["count_rows"]["jobs_rows"]) == len(synthetic_doc["jobs_rows"])

    r = await _get(admin, f"{base}/tables/people", params={"offset": 1, "limit": 2})
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == len(synthetic_doc["people_rows"]) and len(page["rows"]) == 2
    assert page["rows"][0][0]["text"] == "2"
    assert page["columns"][9]["kind"] == "link"
    r = await _get(admin, f"{base}/tables/nope")
    assert r.status_code == 404

    entry = next(f for f in manifest["files"] if f["name"] == "Acme_People_List.xlsx")
    r = await _get(admin, f"{base}/files/Acme_People_List.xlsx")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="Acme_People_List.xlsx"'
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert hashlib.sha256(r.content).hexdigest() == entry["sha256"] == r.headers["x-content-sha256"]
    r = await _get(admin, f"{base}/files/Acme_Internal_Only.docx")
    assert r.status_code == 200
    r = await _get(admin, f"{base}/files/manifest.json")
    assert r.status_code == 404

    stranger = make_world()
    r = await _get(plane_for(stranger.tenant, store), f"{base}/bundle")
    assert r.status_code == 404

    person = synthetic_doc["people_rows"][0]["name"]
    r = await _post(
        admin,
        "POST",
        f"{base}/edits",
        json={"overrides": [{"name": person, "priority": "C", "note": "checked by hand"}]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2 and r.json()["status"] == "built"
    r = await _get(admin, f"{base}/bundle")
    edited = r.json()["bundle"]
    assert edited["overrides"] == [
        *synthetic_doc.get("overrides", []),
        {"name": person, "priority": "C", "note": "checked by hand"},
    ]
    assert next(p for p in edited["people_rows"] if p["name"] == person)["priority"] == "C"
    r = await _get(admin, f"{base}/bundle", params={"version": 1})
    assert r.json()["bundle"]["overrides"] == synthetic_doc.get("overrides", [])
    r = await _post(
        admin, "POST", f"{base}/edits", json={"overrides": [{"name": "Nobody", "priority": "A"}]}
    )
    assert r.status_code == 422


async def test_a_failed_gate_records_a_failed_version_and_serves_nothing(
    synthetic_doc: dict[str, Any],
) -> None:
    w = make_world()
    store = MemoryPayloadStore()
    bad = dict(synthetic_doc)
    # a count claim the computed numbers do not carry fails the number check
    bad["prose"] = {
        **synthetic_doc["prose"],
        "glossary": "There are 999 senior people on this list.",
    }
    found = await ParityService(tenant_txn, store, _Clock()).render_version(
        w.tenant, w.account, w.run, bad
    )
    assert found.status == "failed" and not found.gate["passed"]
    assert found.bundle_key in store.objects and len(store.objects) == 1
    admin = plane_for(w.tenant, store)
    base = f"/v1/accounts/{w.domain}/parity"
    assert (await _get(admin, f"{base}/bundle")).status_code == 404
    versions = (await _get(admin, f"{base}/versions")).json()["versions"]
    assert versions[0]["status"] == "failed" and versions[0]["gate_failures"]
