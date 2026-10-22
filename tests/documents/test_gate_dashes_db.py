"""The gate's dash rule follows the builder's: one rule, one owner (staging 2026-10-21).

The export builder enforces the dash rule on a Word file's authored prose and blanks the stored
titles, names and verbatim quotes it prints (``exports.verify.doc_checks``). The gate used to
scan every file whole, so a document the builder allowed (a key person's title with an en dash)
failed delivery. A file the builder made is trusted for that rule; one with no builder record
is scanned whole, so the rule still holds for anything else. SYNTHETIC data only."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from account_intelligence.platform.hermetic import MemoryPayloadStore
from tests.conftest import _make_account
from tests.documents.test_gate_db import CLEAN, _deliver, _gate, _rows, _run, docx

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
WITH_DASH = f"{CLEAN}\n\nVice President – Sales is the sponsor."
BUILT = {"schema_version": 1, "snapshot_hash": "a" * 64, "refs": {}}


async def _failing(tenant: UUID, manifest: dict[str, object] | None) -> list[str]:
    account = _make_account(tenant, f"northwind-{uuid4().hex[:6]}.example")
    run = _run(tenant, account)
    _rows(tenant, run)
    store = MemoryPayloadStore()
    await _deliver(store, tenant, account, run, docx(WITH_DASH), 1, "summary", manifest)
    return (await _gate(store).check(str(tenant), str(run)))["client_dashes"]


async def test_a_document_the_builder_made_is_not_scanned_again(tenant_a: UUID) -> None:
    assert await _failing(tenant_a, BUILT) == []


async def test_a_library_copy_is_not_scanned_again(tenant_a: UUID) -> None:
    assert await _failing(tenant_a, {"source": "library"}) == []


async def test_a_document_with_no_builder_record_is_scanned_whole(tenant_a: UUID) -> None:
    assert await _failing(tenant_a, None) == ["summary:1"]
    assert await _failing(tenant_a, {"note": "x"}) == ["summary:1"]


async def test_a_builder_record_whose_bytes_changed_is_scanned_whole(tenant_a: UUID) -> None:
    """The record is trusted by integrity, not by its label: a file whose stored hash no longer
    matches the bytes read back is not the file the builder verified."""
    from tests.documents.world import admin

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    run = _run(tenant_a, account)
    _rows(tenant_a, run)
    store = MemoryPayloadStore()
    await _deliver(store, tenant_a, account, run, docx(WITH_DASH), 1, "summary", BUILT)
    admin("UPDATE deliverables SET sha256 = repeat('b', 64) WHERE run_id = %s", run)
    assert (await _gate(store).check(str(tenant_a), str(run)))["client_dashes"] == ["summary:1"]
