"""The database side of the degrade: which accounts count as "other accounts" for the restricted
terms, the rebuild of a run whose first build produced nothing, and the gate and the file listing
for a file the builder left out of the set. Postgres, synthetic data.

Staging 2026-10-31: every account of the tenant, including one the user had removed and one with
the same domain or name as the researched account (another user's private workspace), counted as
"another account"; its name or domain is in every file of the research (titles, links), so the
final scan refused the files."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from documents.gate import CLIENT_FILES, withheld_files
from exports.errors import NotConfigured
from exports.readers.pg import PgContentReader
from tests.conftest import _make_account
from tests.documents.test_gate_db import CLEAN, _deliver, _gate, _rows, _run, docx
from tests.exports.test_db import builder, seed_world
from tests.tracker.world import admin_dsn, fetch

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
OTHER = "Zorblax Industries"


def _account(
    tenant: UUID,
    name: str | None,
    domain: str,
    *,
    owner: str = "other-user",
    removed: bool = False,
) -> UUID:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        row = c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain, name, deleted_at, deleted_by)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                tenant,
                owner,
                domain,
                name,
                datetime.now(UTC) if removed else None,
                "test-owner" if removed else None,
            ),
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def test_the_other_accounts_leave_out_removed_ones_and_the_same_company() -> None:
    w, payloads = await seed_world()
    # the researched account is "Synthetic DB Ltd" on w.domain (seed_rows)
    _account(w.tenant, OTHER, "zorblax.example")  # another account: kept
    _account(w.tenant, "Quuxcorp Ltd", "quuxcorp.example", removed=True)  # removed: left out
    # the same company in another user's private workspace: same domain, another spelling
    _account(w.tenant, "DB Holding Ltd", w.domain, owner="user-two")
    # the same name on another domain: its name is this account's own, its domain is not
    _account(w.tenant, "synthetic db ltd", "synthetic-db-other.example", owner="user-three")
    _account(w.tenant, None, "nameless.example", owner="user-four")  # no name: only its domain
    content = await PgContentReader(payloads, batch=2).read(str(w.tenant), str(w.run))
    terms = set(content.forbidden_terms)
    # old code: all of them, so the account's own domain and name were "forbidden" in every file
    assert {OTHER, "zorblax.example", "synthetic-db-other.example", "nameless.example"} <= terms
    assert not terms & {"Quuxcorp Ltd", "quuxcorp.example", "DB Holding Ltd", w.domain}
    assert "synthetic db ltd" not in {t.casefold() for t in terms}


async def test_the_tracker_adapter_lists_the_same_other_domains() -> None:
    from account_intelligence.tracker.adapters import RunInputAdapter

    w, _ = await seed_world()
    _account(w.tenant, OTHER, "zorblax.example")
    _account(w.tenant, "Quuxcorp Ltd", "quuxcorp.example", removed=True)
    _account(w.tenant, "DB Holding Ltd", w.domain, owner="user-two")
    async with tenant_txn(w.tenant) as s:
        names = await RunInputAdapter(s).other_account_names(w.tenant, w.run)
    assert "zorblax.example" in names
    assert "quuxcorp.example" not in names and w.domain not in names


async def test_a_research_with_the_same_company_listed_twice_builds_every_file() -> None:
    w, payloads = await seed_world()
    _account(w.tenant, "Synthetic DB Ltd", w.domain, owner="user-two")  # a duplicate listing
    _account(w.tenant, OTHER, "zorblax.example")
    result = await builder(w, payloads).build_all(str(w.tenant), str(w.run))
    assert result.status == "complete", result.errors
    assert not result.withheld_files
    kinds = [
        r[0] for r in fetch("SELECT kind FROM deliverables WHERE run_id = %s ORDER BY kind", w.run)
    ]
    assert kinds == ["evidence", "jobs", "people", "summary", "tech", "workspace"]


async def test_a_run_whose_first_build_produced_nothing_is_rebuilt_from_stored_data() -> None:
    """The rebuild (``POST /v1/exports``, the website's rebuild) calls ``build_all`` on the stored
    rows: a build that stored nothing leaves nothing to clear, and the next one stores the set."""
    w, payloads = await seed_world()
    broken = builder(w, payloads)
    broken.renderer = None  # the first build: no renderer, nothing is built
    first = await broken.build_all(str(w.tenant), str(w.run))
    assert first.status == "not_configured" and first.deliverables == ()
    assert fetch("SELECT count(*) FROM deliverables WHERE run_id = %s", w.run)[0][0] == 0
    with pytest.raises(NotConfigured):
        await broken.build(str(w.tenant), str(w.run))
    again = await builder(w, payloads).build_all(str(w.tenant), str(w.run))
    assert again.status == "complete", again.errors
    assert fetch("SELECT count(*) FROM deliverables WHERE run_id = %s", w.run)[0][0] == 6


def test_the_rebuilds_and_the_research_use_the_one_builder() -> None:
    from account_intelligence.api.routes import account_workspace, exports
    from agents.production import delivery

    assert "builder.build_all(" in inspect.getsource(exports._deliverables)
    assert "builder.build_all(" in inspect.getsource(account_workspace.refresh_stale_files)
    assert "builder.build(" in inspect.getsource(delivery.ExportBuilderPort.build)


# ---- the gate and the file listing ------------------------------------------------------------


async def test_a_file_the_builder_left_out_is_held_by_name_not_reported_missing(
    tenant_a: UUID,
) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    run = _run(tenant_a, account)
    _rows(tenant_a, run)
    manifest = {"files_withheld": ["summary:missing_narrative"], "exclusion_hash": ""}
    for kind in sorted(CLIENT_FILES - {"summary"}):
        await _deliver(store, tenant_a, account, run, docx(CLEAN), 1, kind, manifest)
    failing = await _gate(store).check(str(tenant_a), str(run))
    # old code: deliverable_set ["summary"], a run-level failure that held every file back
    assert failing["deliverable_set"] == []
    assert failing["client_output"] == ["summary:missing_narrative"]
    assert not failing["tracker_rows"]
    held = withheld_files([{"check": "client_output", "item": "summary:missing_narrative"}])
    assert held == {"summary": ["client_output"]}


async def test_an_older_file_does_not_stand_in_for_the_one_the_latest_build_left_out(
    tenant_a: UUID,
) -> None:
    from account_intelligence.replica import workspace as W

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    earlier, later = _run(tenant_a, account), _run(tenant_a, account)
    for kind in sorted(CLIENT_FILES):
        await _deliver(store, tenant_a, account, earlier, docx(CLEAN), 1, kind, {})
    manifest = {"files_withheld": ["summary:missing_narrative"], "exclusion_hash": ""}
    for kind in sorted(CLIENT_FILES - {"summary"}):
        await _deliver(store, tenant_a, account, later, docx(CLEAN + " again"), 2, kind, manifest)
    async with tenant_txn(tenant_a) as s:
        listed = [d["kind"] for d in await W.deliverables(s, account)]
    assert sorted(listed) == sorted(CLIENT_FILES - {"summary"}), listed
    assert "missing_narrative" in W.BUILD_RULE_TEXT
    text = W.withheld_text([{"check": "client_output", "item": "summary:missing_narrative"}])
    assert W.DELIVERABLE_LABEL["summary"] in text and "narrative" in text


async def test_a_left_out_kind_is_not_a_reason_to_build_again_on_every_page_view(
    tenant_a: UUID,
) -> None:
    from account_intelligence.replica import workspace as W

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    earlier, later = _run(tenant_a, account), _run(tenant_a, account)
    for kind in sorted(CLIENT_FILES):  # built before an exclusion was added: stale
        await _deliver(
            store, tenant_a, account, earlier, docx(CLEAN), 1, kind, {"exclusion_hash": "a" * 16}
        )
    manifest = {"files_withheld": ["summary:missing_narrative"], "exclusion_hash": ""}
    for kind in sorted(CLIENT_FILES - {"summary"}):  # the build under today's exclusions
        await _deliver(store, tenant_a, account, later, docx(CLEAN + " again"), 2, kind, manifest)
    async with tenant_txn(tenant_a) as s:
        # the old Summary is stale, but the latest build left the Summary out: nothing to rebuild
        assert await W.stale_kinds(s, account) == []
