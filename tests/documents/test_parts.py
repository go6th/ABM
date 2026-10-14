"""Files of any size, read in parts (Phase 4B section 3): a document split into many parts reads
exactly the passages a whole-file read gives, a worker killed in the middle of a file resumes
without one duplicate passage, a failed part is tried again on its own, and the client's part
slots are never exceeded. SYNTHETIC data only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents import repo
from documents.parts import Bounded, bounded, plan
from documents.sandbox import SandboxFailed
from tests.conftest import _make_account
from tests.documents.world import admin, doc_row, service, upload

ROOT = Path(__file__).resolve().parents[2]


def _crm_csv(rows: int) -> bytes:
    """Contacts with quoted values, some spanning lines (a split must never cut a record)."""
    out = ["Name,Company,Email,Note\n"]
    for i in range(rows):
        note = f'"Met at the summit.\nFollow up in Q{1 + i % 4}, ""priority"" {i % 3}"'
        out.append(f"Person {i},Northwind Traders,p{i}@northwind.example,{note}\n")
    return "".join(out).encode()


def _notes_md(sections: int) -> bytes:
    body = []
    for i in range(sections):
        body.append(f"# Meeting {i}\n")
        body.extend(f"Line {j} of meeting {i}: budget review with Northwind.\n" for j in range(40))
    return "".join(body).encode()


def _small_parts(svc: Any, part_bytes: int) -> None:
    svc.cfg = svc.c.model_copy(
        update={"parsing": svc.c.parsing.model_copy(update={"part_bytes": part_bytes})}
    )


def _passages(doc: UUID) -> list[tuple[Any, ...]]:
    return admin(
        "SELECT kind, location, heading_path, content FROM document_passages"
        " WHERE document_id = %s ORDER BY seq",
        doc,
    )


def test_the_planner_splits_at_record_boundaries_only() -> None:
    import io

    data = _crm_csv(300)
    parts = plan(io.BytesIO(data), "csv", part_bytes=2048, pages_per_part=200)
    assert len(parts) > 5
    for before, after in zip(parts, parts[1:], strict=False):
        assert before.end_offset == after.cursor["offset"]
        # each boundary is a record end: the next part starts with a whole record
        assert data[after.cursor["offset"] :].startswith(b"Person ")
        assert after.cursor["header"] == ["Name", "Company", "Email", "Note"]
    assert parts[-1].end_offset is None
    rows = [p.cursor["row"] for p in parts[1:]]
    assert rows == sorted(rows) and rows[0] > 1
    # a bounded reader ends exactly at the part's end
    raw = io.BytesIO(data)
    view = bounded(raw, parts[0].end_offset)
    assert view.read() == data[: parts[0].end_offset]
    assert Bounded(io.BytesIO(data), 10).seek(0, io.SEEK_END) == 10


def test_text_parts_carry_the_line_number_and_heading() -> None:
    import io

    data = _notes_md(30)
    parts = plan(io.BytesIO(data), "md", part_bytes=4096, pages_per_part=200)
    assert len(parts) > 3
    # a Markdown part starts where the parser starts a unit: at a heading
    assert all(data[p.cursor["offset"] :].startswith(b"# Meeting ") for p in parts[1:])
    lines = [p.cursor["line"] for p in parts[1:]]
    assert all(data[: p.cursor["offset"]].count(b"\n") == p.cursor["line"] for p in parts[1:])
    assert lines == sorted(lines)
    # a long section is cut where the parser's unit fills up, the heading carried on
    long = b"# Only\n" + b"".join(b"words words words words %d\n" % i for i in range(4000))
    cut = plan(io.BytesIO(long), "md", part_bytes=8192, pages_per_part=200, unit_text_bytes=4096)
    assert len(cut) > 3 and all(p.cursor["heading"] == "Only" for p in cut[1:])


@pytest.mark.asyncio
@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("name", "make"), [("crm.csv", lambda: _crm_csv(400)), ("notes.md", lambda: _notes_md(40))]
)
async def test_parts_read_the_same_passages_as_the_whole_file(
    name: str, make: Any, tenant_a: UUID, tmp_path: Path
) -> None:
    data = make()
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    whole = service(tmp_path / "whole")
    one = UUID((await upload(whole, tenant_a, name, data, account))["document_id"])
    assert await whole.process(tenant_a, one) == "indexed"
    split = service(tmp_path / "split")
    _small_parts(split, 4096)
    many = UUID((await upload(split, tenant_a, name, data, account))["document_id"])
    assert await split.process(tenant_a, many) == "indexed"
    (parts,) = admin("SELECT parts_total FROM documents WHERE id = %s", many)
    assert parts[0] > 3
    assert _passages(many) == _passages(one)
    assert doc_row(many)["passages"] == doc_row(one)["passages"]
    assert admin("SELECT count(*) FROM chunks WHERE doc_id = %s", many) == admin(
        "SELECT count(*) FROM chunks WHERE doc_id = %s", one
    )
    view = await split.status(tenant_a, many)
    assert (view["parts_done"], view["parts_total"], view["parts_failed"]) == (
        parts[0],
        parts[0],
        [],
    )


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_a_worker_killed_mid_file_resumes_without_duplicate_passages(
    tenant_a: UUID, tmp_path: Path
) -> None:
    data = _crm_csv(1500)
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    clean = service(tmp_path / "clean")
    _small_parts(clean, 16384)
    ref = UUID((await upload(clean, tenant_a, "crm.csv", data, account))["document_id"])
    assert await clean.process(tenant_a, ref) == "indexed"
    expected = _passages(ref)

    svc = service(tmp_path / "killed")
    _small_parts(svc, 16384)
    doc = UUID((await upload(svc, tenant_a, "crm.csv", data, account))["document_id"])
    # the worker dies (SIGKILL semantics) after its 3rd passage batch is committed
    died = subprocess.run(
        [sys.executable, "-m", "tests.documents.kill_worker", str(tenant_a), str(doc), "3",
         str(tmp_path / "killed")],
        cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert died.returncode == 137, died.stderr[-2000:]
    (row,) = admin(
        "SELECT count(*) FILTER (WHERE status = 'done'), count(*) FILTER (WHERE status ="
        " 'running') FROM document_parts WHERE document_id = %s",
        doc,
    )
    assert row[1] == 1  # one part was being read when the worker died
    partial = admin("SELECT count(*) FROM document_passages WHERE document_id = %s", doc)[0][0]
    assert 0 < partial < len(expected)
    # its lease runs out; any worker picks the file up where it stopped
    admin(
        "UPDATE document_parts SET lease_expires_at = now() - interval '1 second'"
        " WHERE document_id = %s AND status = 'running'",
        doc,
    )
    admin("UPDATE documents SET lease_owner = NULL WHERE id = %s", doc)
    # the sweeper sees the abandoned part and the parts still waiting (identifiers only)
    orphaned = admin(
        "SELECT tenant_id, part_no FROM document_parts_orphaned(now()) WHERE document_id = %s", doc
    )
    unfinished = admin(
        "SELECT part_no FROM document_parts WHERE document_id = %s AND status <> 'done'", doc
    )
    assert sorted(orphaned) == sorted((tenant_a, n) for (n,) in unfinished)
    assert await svc.process(tenant_a, doc) == "indexed"
    assert _passages(doc) == expected
    assert admin(
        "SELECT count(*) - count(DISTINCT (location, content)) FROM document_passages"
        " WHERE document_id = %s",
        doc,
    ) == [(0,)]


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_a_failed_part_is_tried_again_on_its_own(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from documents import service as service_module

    data = _crm_csv(600)
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    _small_parts(svc, 16384)
    doc = UUID((await upload(svc, tenant_a, "crm.csv", data, account))["document_id"])
    real = service_module.parse_in_sandbox

    async def flaky(*args: Any, part: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if (part or {}).get("cursor", {}).get("offset") and not flaky.failed:  # type: ignore[attr-defined]
            flaky.failed = True  # type: ignore[attr-defined]
            raise SandboxFailed("parse_timeout")
        async for unit in real(*args, part=part, **kwargs):
            yield unit

    flaky.failed = False  # type: ignore[attr-defined]
    monkeypatch.setattr(service_module, "parse_in_sandbox", flaky)
    assert await svc.process(tenant_a, doc) == "failed"
    view = await svc.status(tenant_a, doc)
    assert view["parts_failed"] == [{"part": 2, "reason": "This part took too long to read."}]
    assert view["message"] == "Some parts of the file could not be read. Try those parts again."
    assert view["parts_done"] == view["parts_total"] - 1
    after = await svc.retry_part(tenant_a, doc, 1)
    assert after["status"] == "parsing" and after["parts_failed"] == []
    assert await svc.process(tenant_a, doc) == "indexed"
    assert doc_row(doc)["passages"] == 600


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_a_client_never_runs_more_parts_at_once_than_its_limit(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = service(tmp_path)
    _small_parts(svc, 4096)
    doc = UUID((await upload(svc, tenant_a, "crm.csv", _crm_csv(300)))["document_id"])
    async with tenant_txn(tenant_a) as s:
        await repo.create_parts(s, doc, [{"cursor": {}}] * 6)
    claimed = []
    for n in range(3):
        async with tenant_txn(tenant_a) as s:
            claimed.append(await repo.claim_part(s, doc, f"worker-{n}", 300, limit=2))
    assert [getattr(c, "part_no", c) for c in claimed] == [0, 1, "busy"]
    async with tenant_txn(tenant_a) as s:
        # the limit counts other workers' live parts: a finished part frees its slot
        assert await repo.finish_part(s, doc, 0, "worker-0", 0, {})
    async with tenant_txn(tenant_a) as s:
        assert (await repo.claim_part(s, doc, "worker-2", 300, limit=2)).part_no == 2


@pytest.mark.asyncio
async def test_the_sandbox_child_gets_only_the_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed image has no ``config/`` beside the package, so the child must be told where
    the config is (found by the Phase 4B click-through: every upload failed in the containers with
    ``FileNotFoundError``); nothing else from the parent's environment reaches it."""
    import asyncio

    from account_intelligence.config import CONFIG_DIR
    from documents import sandbox

    seen: dict[str, Any] = {}

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise OSError("stop before the child runs")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-child")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(OSError):
        await sandbox.plan_in_sandbox(
            "/nonexistent",
            "md",
            "a.md",
            timeout_s=1,
            memory_mb=64,
            cpu_s=1,
            part_bytes=1024,
            pages_per_part=1,
        )
    assert seen["env"] == {"PYTHONHASHSEED": "0", "LANG": "C.UTF-8", "CONFIG_DIR": str(CONFIG_DIR)}
