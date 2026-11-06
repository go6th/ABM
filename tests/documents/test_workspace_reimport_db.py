"""Importing the same upload twice changes no row count (PR3 of the upload hardening,
2026-11-04). SYNTHETIC data only.

The workspace zip of the import tests goes through the production path once; then the import runs
again on the same document, the way the sweep's retry and a "Try again" do. Every tenant table
(read from the catalog, so a writer added later is covered) holds exactly as many rows of the
client after the second import as after the first: each writer inserts a row only when the run
holds none with its natural key (the table at the top of ``documents.workspace_import``).

GUARANTEE(test_importing_the_same_upload_twice_gives_identical_row_counts)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from documents import repo
from documents.deliverables import import_document
from tests.conftest import ADMIN_DSN, _make_account
from tests.documents import workspace_zip
from tests.documents.test_workspace_zip_import_db import _zip_with_twins
from tests.documents.world import service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

# rows that record the passing of time, not the imported content: one per pass by design
PER_PASS = frozenset({"audit_events", "usage_events", "import_runs", "import_run_parts"})


def _counts(tenant: UUID) -> dict[str, int]:
    """Rows of the tenant in every table with a ``tenant_id`` column."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tables = [
            name
            for (name,) in c.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
                " AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
                "   AND a.attname = 'tenant_id' AND NOT a.attisdropped)"
            ).fetchall()
        ]
        return {
            name: int(
                c.execute(
                    f'SELECT count(*) FROM "{name}" WHERE tenant_id = %s',  # noqa: S608 catalog names
                    (tenant,),
                ).fetchone()[0]  # type: ignore[index]
            )
            for name in tables
            if name not in PER_PASS
        }


async def test_importing_the_same_upload_twice_gives_identical_row_counts(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """See the module docstring."""
    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "indexed"
    first = _counts(tenant_a)
    assert first["job_postings"] > 0 and first["people"] > 0 and first["tech_detections"] > 0
    assert first["sources"] > 0 and first["facts"] > 0

    async with tenant_txn(tenant_a) as s:
        doc = await repo.get(s, document)
    counts = await import_document(
        tenant_txn, tenant_a, doc, now=datetime.now(UTC), payloads=svc.payloads, blobs=svc.blobs
    )
    assert not any(k.endswith("_failed") for k in counts), counts
    second = _counts(tenant_a)
    changed = {t: (first[t], second.get(t)) for t in first if first[t] != second.get(t)}
    assert not changed, f"tables whose row count changed on the second import: {changed}"


def _admin(sql: str, *args: object) -> list[tuple[object, ...]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, args)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []


async def test_a_second_import_adds_nothing_and_never_brings_back_an_erased_person(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """The research tables are insert-only for the runtime role (``job_postings``: "postings are
    observations", 0010): each writer inserts a row only when the run has none with its natural
    key. So a second import adds no posting, no sentence and no person, leaves the stored rows as
    they are, and a person erased in between stays erased (never inserted again)."""
    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "indexed"
    ((run,),) = _admin(
        "SELECT id FROM account_runs WHERE account_id = %s AND finished_reason = 'file_import'",
        account,
    )
    ((person,),) = _admin("SELECT id FROM people WHERE run_id = %s ORDER BY name LIMIT 1", run)
    _admin(
        "UPDATE people SET erased_at = now(), erasure_reason = 'test', name = NULL, title = NULL,"
        " headline = NULL, location = NULL WHERE id = %s",
        person,
    )
    before = _counts(tenant_a)

    async with tenant_txn(tenant_a) as s:
        doc = await repo.get(s, document)
    counts = await import_document(
        tenant_txn, tenant_a, doc, now=datetime.now(UTC), payloads=svc.payloads, blobs=svc.blobs
    )
    assert counts["jobs_written"] == counts["people_written"] == 0, counts
    assert _admin("SELECT name, erased_at IS NOT NULL FROM people WHERE id = %s", person) == [
        (None, True)
    ]
    assert _counts(tenant_a) == before
