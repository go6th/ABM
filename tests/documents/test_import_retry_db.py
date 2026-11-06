"""Bounded retry of an import (PR3 of the upload hardening, 2026-11-04). SYNTHETIC data only.

A failed part of a workspace import is tried again by the sweep at most three times in all, with a
growing wait, and only that part: the parts that succeeded keep their rows and their attempts. A
part left running by a worker that died counts as a spent attempt and is tried again. An import
that failed as a whole is also tried at most three times. Nothing here starts the analysis.

GUARANTEE(test_a_failed_part_is_tried_three_times_in_all_and_then_left_failed)
GUARANTEE(test_a_retried_part_that_succeeds_lands_and_the_run_is_ok)
GUARANTEE(test_a_part_left_running_by_a_dead_worker_is_tried_again)
GUARANTEE(test_an_import_that_fails_as_a_whole_is_tried_at_most_three_times)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from documents import deliverables, workspace_import
from tests.documents.test_import_status_db import _import, _parts
from tests.documents.test_workspace_zip_import_db import _zip_with_twins
from tests.documents.world import admin

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _next_attempt(doc: UUID, part: str) -> Any:
    ((when,),) = admin(
        "SELECT p.next_attempt_at FROM import_run_parts p JOIN import_runs r"
        " ON r.tenant_id = p.tenant_id AND r.id = p.import_run_id"
        " WHERE r.document_id = %s AND p.part = %s",
        doc,
        part,
    )
    return when


def _rows(account: UUID) -> dict[str, int]:
    return {
        table: int(admin(f"SELECT count(*) FROM {table} WHERE account_id = %s", account)[0][0])  # noqa: S608 fixed names
        for table in ("job_postings", "people", "tech_detections", "sources")
    }


async def _retry(svc: Any, at: datetime) -> dict[str, int]:
    return await deliverables.retry_import_parts(at, payloads=svc.payloads, blobs=svc.blobs)


async def test_a_failed_part_is_tried_three_times_in_all_and_then_left_failed(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See the module docstring."""
    calls: list[int] = []

    async def boom(*args: Any, **kwargs: Any) -> int:
        calls.append(1)
        raise RuntimeError("synthetic jobs failure")

    monkeypatch.setattr(workspace_import, "import_postings", boom)
    account, doc, svc = await _import(tmp_path, tenant_a, _zip_with_twins())
    parts = _parts(doc)
    assert parts["jobs"]["status"] == "failed" and parts["jobs"]["attempts"] == 1
    assert parts["people"]["status"] == "ok" and parts["people"]["attempts"] == 1
    assert _next_attempt(doc, "jobs") is not None
    rows = _rows(account)
    now = datetime.now(UTC)

    await _retry(svc, now)  # the wait is not over: nothing is tried
    assert len(calls) == 1
    await _retry(svc, now + timedelta(minutes=6))
    assert len(calls) == 2 and _parts(doc)["jobs"]["attempts"] == 2
    await _retry(svc, now + timedelta(minutes=25))
    after = _parts(doc)
    assert len(calls) == 3 and after["jobs"]["attempts"] == 3
    assert after["jobs"]["status"] == "failed" and _next_attempt(doc, "jobs") is None
    await _retry(svc, now + timedelta(hours=3))  # the last attempt was spent: never again
    assert len(calls) == 3
    # the parts that succeeded were never run again and their rows are as they were
    final = _parts(doc)
    for part in ("people", "tech", "docx", "raw"):
        assert final[part]["attempts"] == parts[part]["attempts"], part
    assert _rows(account) == rows
    ((status, attempts),) = admin(
        "SELECT status, attempts FROM import_runs WHERE document_id = %s", doc
    )
    assert status == "failed" and attempts == 3


async def test_a_retried_part_that_succeeds_lands_and_the_run_is_ok(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See the module docstring."""
    real = workspace_import.import_postings
    calls: list[int] = []

    async def once(*args: Any, **kwargs: Any) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic jobs failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(workspace_import, "import_postings", once)
    account, doc, svc = await _import(tmp_path, tenant_a, _zip_with_twins())
    assert _rows(account)["job_postings"] == 0
    done = await _retry(svc, datetime.now(UTC) + timedelta(minutes=6))
    assert done["parts"] >= 1
    parts = _parts(doc)
    assert parts["jobs"]["status"] == "ok" and parts["jobs"]["attempts"] == 2
    assert _rows(account)["job_postings"] > 0
    assert admin("SELECT status FROM import_runs WHERE document_id = %s", doc) == [("ok",)]


async def test_a_part_left_running_by_a_dead_worker_is_tried_again(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """See the module docstring."""
    account, doc, svc = await _import(tmp_path, tenant_a, _zip_with_twins())
    admin(
        "UPDATE import_run_parts p SET status = 'running', finished_at = NULL,"
        " updated_at = now() - interval '2 hours' FROM import_runs r"
        " WHERE r.tenant_id = p.tenant_id AND r.id = p.import_run_id AND r.document_id = %s"
        " AND p.part = 'tech'",
        doc,
    )
    before = _rows(account)
    await _retry(svc, datetime.now(UTC))
    parts = _parts(doc)
    assert parts["tech"]["status"] == "ok" and parts["tech"]["attempts"] == 2
    assert parts["jobs"]["attempts"] == 1  # untouched
    assert _rows(account) == before  # the retried part found its rows already stored


async def test_an_import_that_fails_as_a_whole_is_tried_at_most_three_times(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See the module docstring."""
    seen: list[str] = []

    async def boom(txn: Any, tenant_id: UUID, doc: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(str(doc.id))  # every import of the pass fails; this test counts its own
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(workspace_import, "_import_workspace", boom)
    _, doc, svc = await _import(tmp_path, tenant_a, _zip_with_twins())

    class Calls:
        def __len__(self) -> int:
            return seen.count(str(doc))

    calls = Calls()
    assert len(calls) == 1
    assert admin("SELECT imported_at FROM documents WHERE id = %s", doc) == [(None,)]
    now = datetime.now(UTC)

    async def sweep(at: datetime) -> None:
        await deliverables.backfill_imports(at, payloads=svc.payloads, blobs=svc.blobs)

    await sweep(now)  # the wait is not over
    assert len(calls) == 1
    await sweep(now + timedelta(minutes=6))
    assert len(calls) == 2
    await sweep(now + timedelta(minutes=25))
    assert len(calls) == 3
    await sweep(now + timedelta(hours=3))
    assert len(calls) == 3
    ((status, attempts),) = admin(
        "SELECT status, attempts FROM import_runs WHERE document_id = %s", doc
    )
    assert status == "failed" and attempts == 3
