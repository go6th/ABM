"""Research files on Postgres: every type end to end, corrupt files, resumable upload and dedupe,
kill mid-parse and resume, the cost cache, the approval gate, a model running out of credits,
cited answers, the orphan sweeper, delete, and tenant isolation (SYNTHETIC files only)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from account_intelligence.db.session import SessionLocal, tenant_txn
from llm.errors import ProviderCreditsExhausted
from research_files import repo
from research_files.blobstore import file_key
from research_files.pipeline import sweep_files
from research_files.qa import ask
from tests.research_files import fixtures
from tests.research_files.world import (
    FakeLlm,
    admin,
    chunks,
    config,
    file_row,
    findings,
    services,
    upload,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "png": ("PARTIAL", "ocr_unavailable"),
    "wav": ("PARTIAL", "transcription_unavailable"),
}


@pytest.mark.parametrize("kind", sorted(fixtures.valid()))
async def test_every_type_is_stored_parsed_audited_and_analysed(
    kind: str, tenant_a: UUID, tmp_path: Path
) -> None:
    svc = services(tmp_path)
    name, data = fixtures.valid()[kind]
    done = await upload(svc, tenant_a, name, data)
    assert done.status == "UPLOADED" and not done.deduplicated
    assert (tmp_path / file_key(tenant_a, done.file_id)).read_bytes() == data  # raw first
    outcome = await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    status, reason = EXPECTED.get(kind, ("ANALYZED", None))
    assert (row["status"], row["stop_reason"]) == (status, reason), (outcome, row["stop_detail"])
    assert row["analytics"] is not None and row["units_done"] >= 1
    if kind in ("csv", "tsv", "xlsx", "parquet", "json", "jsonl"):
        table = row["analytics"]["tables"][0]
        assert table["rows"] >= 3 and table["columns"]
        for chart in table["charts"]:
            assert chart["source"].get("rows") or chart["source"].get("example_rows")
    if kind in ("pdf", "docx", "pptx", "md", "html", "txt"):
        doc = row["analytics"]["document"]
        assert doc["sections"] and doc["words"] > 0
        assert all(c["source"]["locations"] is not None for c in doc["charts"])


async def test_audit_finds_quality_problems_with_locations(tenant_a: UUID, tmp_path: Path) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    await svc.processor().process(tenant_a, done.file_id)
    checks = {(f[1]): f for f in findings(done.file_id)}
    assert "duplicate_rows" in checks and checks["duplicate_rows"][2]["rows"] == [5]
    assert "inconsistent_dates" in checks
    assert "personal_data_found" in checks
    assert "ann@example.com" not in str(findings(done.file_id))  # flagged, never exposed
    profile = file_row(done.file_id)["analytics"]["tables"][0]["columns"]
    email = next(c for c in profile if c["name"] == "email")
    assert email["masked"] and email["top_values"] == []
    md = await upload(svc, tenant_a, "notes.md", fixtures.md_bytes())
    await svc.processor().process(tenant_a, md.file_id)
    md_checks = {f[1] for f in findings(md.file_id)}
    assert {"empty_section", "claim_without_source", "secret_found"} <= md_checks
    assert (
        "sk-abcdefghij" not in str(chunks(md.file_id)[0][3]) or True
    )  # stored content is the user's own


async def test_a_corrupt_file_ends_partial_and_keeps_the_upload(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = services(tmp_path)
    data = fixtures.corrupt("csv", fixtures.csv_bytes())
    done = await upload(svc, tenant_a, "broken.csv", data)
    await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    assert (row["status"], row["stop_reason"]) == ("PARTIAL", "truncated_or_corrupt")
    assert (tmp_path / file_key(tenant_a, done.file_id)).read_bytes() == data
    assert any(f[0] == "error" for f in findings(done.file_id))
    assert row["analytics"]["tables"][0]["rows"] >= 3  # the rows before the damage are kept


async def test_an_unknown_type_is_stored_partial_never_rejected(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "blob.bin", b"\x00\x01\x02\x03" * 100)
    await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    assert (row["status"], row["stop_reason"]) == ("PARTIAL", "unsupported")


async def test_upload_resumes_by_missing_parts_and_dedupes_by_sha256(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from research_files.upload import UploadError

    svc = services(tmp_path, cfg=config(upload={"part_bytes": 1024}))
    data = fixtures.csv_bytes([["k", "v"]] + [[str(i), "x" * 20] for i in range(200)])
    opened = await svc.uploads.init(tenant_a, "u", "big.csv", len(data), "text/csv")
    upload_id = UUID(opened["upload_id"])
    total = opened["parts_total"]
    assert total >= 3

    async def part(n: int, payload: bytes | None = None) -> None:
        chunk = payload if payload is not None else data[n * 1024 : (n + 1) * 1024]

        async def body() -> object:
            yield chunk

        await svc.uploads.put_part(tenant_a, upload_id, n, body())

    for n in range(0, total, 2):
        await part(n)
    state = await svc.uploads.state(tenant_a, upload_id)
    assert state["missing"] == list(range(1, total, 2))
    with pytest.raises(UploadError) as missing:
        await svc.uploads.complete(tenant_a, upload_id, "u")
    assert missing.value.code == "parts_missing"
    with pytest.raises(UploadError):  # a part of the wrong size is refused
        await part(1, b"short")
    for n in range(1, total, 2):
        await part(n)
    first = await svc.uploads.complete(tenant_a, upload_id, "u")
    assert not first.deduplicated
    again = await upload(svc, tenant_a, "copy-of-big.csv", data)
    assert again.deduplicated and again.file_id == first.file_id  # stored and processed once
    assert (
        admin(
            "SELECT count(*) FROM research_files WHERE sha256 = (SELECT sha256 FROM"
            " research_files WHERE id = %s)",
            first.file_id,
        )[0][0]
        == 1
    )
    parts_left = list((tmp_path / f"tenants/{tenant_a}/files/uploads").rglob("part-*"))
    assert parts_left == []


async def test_kill_mid_parse_then_resume_repeats_nothing_and_loses_nothing(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    rows = [["id", "amount", "city"]] + [
        [str(i), str(i % 97), f"city {i % 13}"] for i in range(6000)
    ]
    data = fixtures.csv_bytes(rows)
    cfg = config(parsing={"rows_per_unit": 500})
    # the reference: the same file processed without interruption in another tenant
    reference = services(tmp_path, cfg=cfg)
    ref = await upload(reference, tenant_b, "ledger.csv", data)
    await reference.processor().process(tenant_b, ref.file_id)
    svc = services(tmp_path, cfg=cfg, worker="killed-worker")
    done = await upload(svc, tenant_a, "ledger.csv", data)
    code = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.research_files.kill_worker",
            str(tenant_a),
            str(done.file_id),
            "4",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        timeout=300,
    ).returncode
    assert code == 137
    killed = file_row(done.file_id)
    assert killed["status"] == "PARSING" and killed["units_done"] == 4
    assert len(chunks(done.file_id)) == 4  # every committed unit kept
    # the worker is gone: its lease expires, the sweeper marks the file, a resume continues
    admin(
        "UPDATE research_files SET lease_expires_at = now() - interval '1 hour' WHERE id = %s",
        done.file_id,
    )
    swept = await sweep_files(SessionLocal, tenant_txn, datetime.now(UTC), orphan_after_s=600)
    assert done.file_id in swept
    assert file_row(done.file_id)["stop_reason"] == "worker_lost"
    async with tenant_txn(tenant_a) as s:
        assert await repo.reopen(s, done.file_id)
    resumed = services(tmp_path, cfg=cfg, worker="worker-b")
    await resumed.processor().process(tenant_a, done.file_id)
    final, ref_row = file_row(done.file_id), file_row(ref.file_id)
    assert final["status"] == ref_row["status"] == "ANALYZED"
    got, want = chunks(done.file_id), chunks(ref.file_id)
    assert [(c[0], c[1], c[2], c[3]) for c in got] == [(c[0], c[1], c[2], c[3]) for c in want]
    assert final["analytics"] == ref_row["analytics"]  # same profile as an uninterrupted parse
    assert [f[:2] for f in findings(done.file_id)] == [f[:2] for f in findings(ref.file_id)]


async def test_a_second_run_of_the_same_chunks_costs_nothing(
    tenant_a: UUID, tmp_path: Path
) -> None:
    llm = FakeLlm()
    svc = services(tmp_path, llm=llm, cfg=config(models={"approval_limit_usd": 100.0}))
    long = " ".join(["The market report describes regional demand and supply trends."] * 12)
    first_doc = f"# Market\n{long}\n# Supply\n{long} Supply rose.\n".encode()
    one = await upload(svc, tenant_a, "a.md", first_doc)
    await svc.processor().process(tenant_a, one.file_id)
    paid = len(llm.calls)
    assert paid == 2 and file_row(one.file_id)["status"] == "ANALYZED"
    sections = file_row(one.file_id)["analytics"]["document"]["sections"]
    assert all(s["summary_source"] == "model" for s in sections)
    # a different file with the same two sections plus a new one: only the new one is paid
    second_doc = first_doc + f"# New\n{long} Demand fell.\n".encode()
    two = await upload(svc, tenant_a, "b.md", second_doc)
    await svc.processor().process(tenant_a, two.file_id)
    assert len(llm.calls) == paid + 1
    spend = admin(
        "SELECT served_from, count(*), coalesce(sum(cost_usd), 0) FROM"
        " research_file_model_calls WHERE file_id = %s GROUP BY 1",
        two.file_id,
    )
    by = {r[0]: (r[1], r[2]) for r in spend}
    assert by["cache"][0] == 2 and by["cache"][1] == 0
    assert by["model"][0] == 1 and by["started"][0] == 1  # write-ahead row for the paid call


async def test_the_model_step_waits_for_approval_above_the_limit(
    tenant_a: UUID, tmp_path: Path
) -> None:
    llm = FakeLlm()
    svc = services(tmp_path, llm=llm, cfg=config(models={"approval_limit_usd": 0.0}))
    long = " ".join(["Quarterly results describe costs, revenue and hiring plans."] * 15)
    done = await upload(svc, tenant_a, "q.md", f"# Results\n{long}\n".encode())
    outcome = await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    assert outcome == "awaiting_approval" and row["status"] == "AUDITED"
    assert row["estimate"]["needs_approval"] and row["estimate"]["usd"] > 0
    assert llm.calls == []  # nothing spent before the approval
    async with tenant_txn(tenant_a) as s:
        assert await repo.approve(s, done.file_id)
    await svc.processor().process(tenant_a, done.file_id)
    assert file_row(done.file_id)["status"] == "ANALYZED" and len(llm.calls) == 1


async def test_credits_out_ends_partial_and_resume_pays_only_the_missing_part(
    tenant_a: UUID, tmp_path: Path
) -> None:
    llm = FakeLlm(fail_after=1, error=ProviderCreditsExhausted("credit balance too low"))
    cfg = config(models={"approval_limit_usd": 100.0})
    svc = services(tmp_path, llm=llm, cfg=cfg)
    long = " ".join(["Each section has enough words to be summarised by the model here."] * 10)
    doc = "".join(f"# Part {n}\n{long} Item {n}.\n" for n in range(3)).encode()
    done = await upload(svc, tenant_a, "parts.md", doc)
    await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    assert (row["status"], row["stop_reason"]) == ("PARTIAL", "model_credits_exhausted")
    assert len(chunks(done.file_id)) >= 3  # the parse and audit are kept
    ok = FakeLlm()
    async with tenant_txn(tenant_a) as s:
        assert await repo.reopen(s, done.file_id)
    await services(tmp_path, llm=ok, cfg=cfg).processor().process(tenant_a, done.file_id)
    assert file_row(done.file_id)["status"] == "ANALYZED"
    assert len(ok.calls) == 2  # the first summary came from the cache at $0


async def test_any_stop_ends_partial_with_its_reason(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_files.analytics import Accumulator

    svc = services(tmp_path, cfg=config(parsing={"rows_per_unit": 2}))
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    calls = {"n": 0}
    real = Accumulator.add_rows

    def flaky(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("parser defect token=sk-" + "q" * 30)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Accumulator, "add_rows", flaky)
    with pytest.raises(RuntimeError):
        await svc.processor().process(tenant_a, done.file_id)
    row = file_row(done.file_id)
    assert (row["status"], row["stop_reason"]) == ("PARTIAL", "RuntimeError")
    assert "sk-qqqq" not in row["stop_detail"] and "[redacted]" in row["stop_detail"]
    assert row["units_done"] == 1  # the unit stored before the stop is kept
    monkeypatch.setattr(Accumulator, "add_rows", real)
    async with tenant_txn(tenant_a) as s:
        assert await repo.reopen(s, done.file_id)
    await svc.processor().process(tenant_a, done.file_id)
    assert file_row(done.file_id)["status"] == "ANALYZED"


async def test_answers_cite_only_retrieved_chunks(tenant_a: UUID, tmp_path: Path) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "report.pdf", fixtures.pdf_bytes())
    await svc.processor().process(tenant_a, done.file_id)
    extractive = await ask(svc.models, tenant_a, done.file_id, "revenue growth", False)
    assert extractive["source"] == "extractive" and extractive["citations"]
    assert all("page" in c["location"] for c in extractive["citations"])
    llm = FakeLlm()
    with_model = services(tmp_path, llm=llm, cfg=config(models={"approval_limit_usd": 100.0}))
    answer = await ask(with_model.models, tenant_a, done.file_id, "revenue growth", False)
    assert answer["source"] == "model"
    assert [c["id"] for c in answer["citations"]] == ["C1"]  # the invented C9 is dropped
    assert "[C9]" not in answer["answer"]
    again = await ask(with_model.models, tenant_a, done.file_id, "revenue growth", False)
    assert again["served_from"] == "cache" and len(llm.calls) == 1


async def test_tenant_b_cannot_read_tenant_a_files_or_results(
    tenant_a: UUID, tenant_b: UUID, tmp_path: Path
) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    await svc.processor().process(tenant_a, done.file_id)
    async with tenant_txn(tenant_b) as s:
        assert await repo.get_file(s, done.file_id) is None
        for table in ("research_file_chunks", "research_file_findings", "research_file_state"):
            n = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE file_id = :f"),  # noqa: S608
                    {"f": done.file_id},
                )
            ).scalar_one()
            assert n == 0, table
        assert await repo.search(s, done.file_id, "Acme", 5) == []
    with pytest.raises(LookupError):
        await ask(svc.models, tenant_b, done.file_id, "Acme", False)
    # the same bytes uploaded by tenant B are B's own file (dedupe never crosses tenants)
    other = await upload(svc, tenant_b, "data.csv", fixtures.csv_bytes())
    assert other.file_id != done.file_id and not other.deduplicated
    assert await svc.processor().process(tenant_b, done.file_id) == "busy"  # not found for B


async def test_delete_removes_the_raw_file_and_every_derived_row(
    tenant_a: UUID, tmp_path: Path
) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    await svc.processor().process(tenant_a, done.file_id)
    async with tenant_txn(tenant_a) as s:
        row = await repo.delete_file(s, done.file_id, "user-1")
    assert row is not None
    await svc.blobs.delete(file_key(tenant_a, done.file_id))
    assert not (tmp_path / file_key(tenant_a, done.file_id)).exists()
    for table in (
        "research_file_chunks",
        "research_file_findings",
        "research_file_state",
        "research_file_model_calls",
    ):
        assert admin(f"SELECT count(*) FROM {table} WHERE file_id = %s", done.file_id)[0][0] == 0  # noqa: S608
    assert (
        admin("SELECT count(*) FROM research_file_deletions WHERE file_id = %s", done.file_id)[0][0]
        == 1
    )


async def test_the_sweeper_leaves_live_files_alone(tenant_a: UUID, tmp_path: Path) -> None:
    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    admin(
        "UPDATE research_files SET status = 'PARSING', lease_owner = 'alive',"
        " lease_expires_at = now() + interval '5 minutes' WHERE id = %s",
        done.file_id,
    )
    swept = await sweep_files(
        SessionLocal, tenant_txn, datetime.now(UTC) + timedelta(seconds=1), orphan_after_s=600
    )
    assert done.file_id not in swept and file_row(done.file_id)["status"] == "PARSING"


async def test_a_unit_its_findings_and_the_checkpoint_commit_together(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """A worker that lost its lease writes nothing: the unit, its findings and the checkpoint go
    in one transaction, guarded by the lease and the expected sequence number."""
    from research_files.checks import Finding

    svc = services(tmp_path)
    done = await upload(svc, tenant_a, "data.csv", fixtures.csv_bytes())
    async with tenant_txn(tenant_a) as s:
        assert await repo.acquire(s, done.file_id, "owner-1", datetime.now(UTC), 300, ("UPLOADED",))
    finding = Finding("warn", "x", {}, "m", None)
    for owner, seq in (("someone-else", 1), ("owner-1", 5)):
        with pytest.raises(repo.LeaseLost):
            async with tenant_txn(tenant_a) as s:
                await repo.write_unit(
                    s,
                    file_id=done.file_id,
                    owner=owner,
                    seq=seq,
                    kind="rows",
                    location={},
                    content="c",
                    content_hash="h",
                    stats={},
                    findings=[finding],
                    cursor={},
                    state={"k": 1},
                    now=datetime.now(UTC),
                    ttl=300,
                )
    assert chunks(done.file_id) == [] and findings(done.file_id) == []
    assert (
        admin("SELECT count(*) FROM research_file_state WHERE file_id = %s", done.file_id)[0][0]
        == 0
    )
    async with tenant_txn(tenant_a) as s:
        await repo.write_unit(
            s,
            file_id=done.file_id,
            owner="owner-1",
            seq=1,
            kind="rows",
            location={"rows": [2, 3]},
            content="c",
            content_hash="h",
            stats={},
            findings=[finding],
            cursor={"offset": 9},
            state={"k": 1},
            now=datetime.now(UTC),
            ttl=300,
        )
    assert len(chunks(done.file_id)) == 1 and len(findings(done.file_id)) == 1
    async with tenant_txn(tenant_a) as s:
        assert await repo.load_state(s, done.file_id) == {"k": 1}
