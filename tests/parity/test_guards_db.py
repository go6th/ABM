"""The parity bundle obeys the protections of the export build (merge review 2026-10-30).

* its render scans every client file, the zip and the bundle document for the client-output
  denylist and the run's restricted terms (a hit fails the version; nothing of it is kept or
  shown);
* it reads the run under the tenant's CURRENT exclusions and records the set it was built with;
  a version the exclusions have overtaken is not served, nor edited;
* a version that lists a person erased after it was built is not served;
* a version whose run's latest quality check failed is not served (the export files of that run
  are held back too, and the bundle document carries every table at once);
* a deleted account has no versions to read, and adding the domain again brings them back;
* another tenant, and a colleague of the account's owner, read nothing.

Needs Postgres, LibreOffice, Node and the built Word renderer.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.runs import lifecycle
from parity.recalc import soffice_binary
from parity.render import DOCX_CLI
from parity.service import ParityService, ParityStale
from tests.exports.test_db import builder, seed_world
from tests.exports.test_leak_wave_d2 import _add_run, _as_user, _exclude, _gate
from tests.parity.test_service_db import plane_for
from tests.phase3.test_ws4_views import _get, _post
from tests.tracker.world import admin_dsn, make_world

pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        soffice_binary() is None or shutil.which("node") is None or not DOCX_CLI.exists(),
        reason="LibreOffice, Node or the Word renderer is missing",
    ),
]


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def _built() -> tuple[Any, MemoryPayloadStore, ParityService, Any]:
    w, payloads = await seed_world()
    b = builder(w, payloads)
    service = ParityService(tenant_txn, payloads, _Clock(), reader=b.reader)
    found = await service.build_from_run(w.tenant, w.run)
    return w, payloads, service, found


async def test_the_render_scan_fails_a_version_that_carries_a_restricted_term(
    synthetic_doc: dict[str, Any],
) -> None:
    w = make_world()
    store = MemoryPayloadStore()
    leaky = json.loads(json.dumps(synthetic_doc))
    leaky["jobs_rows"][0]["description"] = "Reports to the team of Zeta Holdings Incorporated."
    found = await ParityService(tenant_txn, store, _Clock()).render_version(
        w.tenant, w.account, w.run, leaky, restricted=("Zeta Holdings Incorporated",)
    )
    assert found.status == "failed"
    scan = next(c for c in found.gate["checks"] if c["name"] == "client_output_scan")
    assert scan["status"] == "FAIL"
    # the report names files and counts, never the term; nothing of the version is stored
    assert "Zeta" not in json.dumps(found.gate)
    assert store.objects == {}
    admin = plane_for(w.tenant, store)
    base = f"/v1/accounts/{w.domain}/parity"
    assert (await _get(admin, f"{base}/bundle")).status_code == 404
    versions = (await _get(admin, f"{base}/versions")).json()["versions"]
    assert versions[0]["status"] == "failed" and "Zeta" not in json.dumps(versions)


async def test_the_render_scan_applies_the_denylist_to_the_rendered_files(
    synthetic_doc: dict[str, Any],
) -> None:
    """Credit counts are on the denylist the export build uses; the parity gate's own scan
    only warns about the word."""
    w = make_world()
    store = MemoryPayloadStore()
    leaky = json.loads(json.dumps(synthetic_doc))
    leaky["jobs_rows"][0]["description"] = "The research used 120 credits for this page."
    found = await ParityService(tenant_txn, store, _Clock()).render_version(
        w.tenant, w.account, w.run, leaky
    )
    assert found.status == "failed"
    assert next(c for c in found.gate["checks"] if c["name"] == "client_output_scan")["findings"]
    assert found.gate["checks"][0]["name"] == "third_party_scan"  # which only warned


async def test_a_clean_render_passes_the_scan(synthetic_doc: dict[str, Any]) -> None:
    w = make_world()
    found = await ParityService(tenant_txn, MemoryPayloadStore(), _Clock()).render_version(
        w.tenant, w.account, w.run, synthetic_doc, restricted=("Zeta Holdings Incorporated",)
    )
    assert found.status == "built"
    scan = next(c for c in found.gate["checks"] if c["name"] == "client_output_scan")
    assert scan["status"] == "PASS"


async def test_a_new_exclusion_holds_the_version_back_until_the_account_is_built_again() -> None:
    w, payloads, service, found = await _built()
    assert found.status == "built" and found.manifest["exclusion_hash"] == "none"
    base = f"/v1/accounts/{w.domain}/parity"
    admin = plane_for(w.tenant, payloads)
    assert (await _get(admin, f"{base}/bundle")).status_code == 200
    names = [
        p["name"] for p in (await _get(admin, f"{base}/bundle")).json()["bundle"]["people_rows"]
    ]
    assert names, "the synthetic run must list people"
    await _exclude(w, people=(names[0],))
    # served nowhere, listed as held back, and an edit of it is refused
    for route in ("bundle", "manifest", "screen", "tables/people"):
        assert (await _get(admin, f"{base}/{route}")).status_code == 404, route
    assert (
        await _get(admin, f"{base}/files/{found.manifest['files'][0]['name']}")
    ).status_code == 404
    versions = (await _get(admin, f"{base}/versions")).json()["versions"]
    assert versions[0]["status"] == "built" and versions[0]["held_back"] == "exclusions"
    r = await _post(admin, "POST", f"{base}/edits", json={"overrides": [{"name": names[0]}]})
    assert r.status_code == 404
    with pytest.raises(ParityStale):
        await service.edit(w.tenant, found, [{"name": names[0], "priority": "A"}])
    # the next build reads the run under the new list: the person is gone from the version
    again = await service.build_from_run(w.tenant, w.run)
    assert again.version == 2 and again.manifest["exclusion_hash"] != "none"
    r = await _get(admin, f"{base}/bundle")
    assert r.status_code == 200 and r.json()["version"] == 2
    assert names[0] not in {p["name"] for p in r.json()["bundle"]["people_rows"]}
    assert names[0] not in json.dumps(r.json()["bundle"]["prose"])


async def test_an_excluded_company_domain_has_no_bundle() -> None:
    w, payloads, _service, _found = await _built()
    admin = plane_for(w.tenant, payloads)
    base = f"/v1/accounts/{w.domain}/parity"
    assert (await _get(admin, f"{base}/versions")).status_code == 200
    await _exclude(w, companies=(w.domain,))
    for route in ("versions", "bundle", "screen"):
        assert (await _get(admin, f"{base}/{route}")).status_code == 404, route


async def test_a_version_that_lists_an_erased_person_is_not_served() -> None:
    w, payloads, _service, found = await _built()
    admin = plane_for(w.tenant, payloads)
    base = f"/v1/accounts/{w.domain}/parity"
    assert (await _get(admin, f"{base}/screen")).status_code == 200
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE people SET name = NULL, title = NULL, headline = NULL, location = NULL,"
            " erased_at = now(), erasure_reason = 'test'"
            " WHERE id = (SELECT id FROM people WHERE account_id = %s ORDER BY id LIMIT 1)",
            (w.account,),
        )
    assert (await _get(admin, f"{base}/screen")).status_code == 404
    versions = (await _get(admin, f"{base}/versions")).json()["versions"]
    assert versions[0]["held_back"] == "erasure" and versions[0]["version"] == found.version


async def test_a_failed_quality_check_holds_the_version_back_like_the_files() -> None:
    """The export build withholds only the file a file-level defect names; the bundle document
    carries every table at once, so any failed verdict holds the whole version back."""
    w, payloads, _service, _found = await _built()
    admin = plane_for(w.tenant, payloads)
    base = f"/v1/accounts/{w.domain}/parity"
    _gate(w, "fail", [{"check": "leak_check", "item": "people:ngram=2"}])
    assert (await _get(admin, f"{base}/screen")).status_code == 404
    _gate(w, "fail", [{"check": "fact_citations", "item": "f1:no_citation"}])
    assert (await _get(admin, f"{base}/screen")).status_code == 404
    versions = (await _get(admin, f"{base}/versions")).json()["versions"]
    assert versions[0]["held_back"] == "quality_check"
    _gate(w, "pass", [])
    assert (await _get(admin, f"{base}/screen")).status_code == 200


async def test_a_deleted_account_has_no_versions_and_adding_it_again_brings_them_back() -> None:
    w, payloads, _service, _found = await _built()
    admin = plane_for(w.tenant, payloads)
    base = f"/v1/accounts/{w.domain}/parity"
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE account_runs SET status = 'COMPLETED', finished_at = now() WHERE id = %s",
            (w.run,),
        )
    with _as_user("test-owner"):
        async with tenant_txn(w.tenant) as s:
            assert await lifecycle.delete_account(s, w.domain, deleted_by="test-owner")
    for route in ("versions", "bundle", "manifest", "screen", "tables/people"):
        assert (await _get(admin, f"{base}/{route}")).status_code == 404, route
    with _as_user("test-owner"):
        async with tenant_txn(w.tenant) as s:
            _id, created = await lifecycle.add_account(s, w.tenant, w.domain, added_by="test-owner")
    assert created is True
    assert (await _get(admin, f"{base}/screen")).status_code == 200


async def test_the_screen_says_when_the_version_is_of_an_earlier_research() -> None:
    w, payloads, _service, found = await _built()
    admin = plane_for(w.tenant, payloads)
    base = f"/v1/accounts/{w.domain}/parity"
    # no delivered file yet: nothing to disagree with
    assert (await _get(admin, f"{base}/screen")).json()["current"] is True
    # the export build of the same run delivers its files: still the same research
    await builder(w, payloads).build_all(str(w.tenant), str(w.run))
    assert (await _get(admin, f"{base}/screen")).json()["current"] is True
    # a later research delivered files of its own; the version is of the earlier one
    later = _add_run(w)
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE deliverables SET run_id = %s WHERE id ="
            " (SELECT id FROM deliverables WHERE account_id = %s ORDER BY built_at DESC LIMIT 1)",
            (later.run, w.account),
        )
    body = (await _get(admin, f"{base}/screen")).json()
    assert body["current"] is False and body["version"] == found.version


async def test_a_second_build_of_the_same_run_adds_no_version() -> None:
    """The build node is retried on a dropped connection and a task can be delivered twice:
    the run's built version stands, and an analyst's later edit of it is not overwritten."""
    w, payloads, service, found = await _built()
    again = await service.build_from_run(w.tenant, w.run)
    assert again.version == found.version == 1
    async with tenant_txn(w.tenant) as s:
        assert len(await service.list(s, w.account)) == 1
