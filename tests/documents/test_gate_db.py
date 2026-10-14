"""The pre-delivery gate on Postgres for both input modes: a clean run passes; an unset tracker
row, a dash used as punctuation, a broker name and a credit count, and a client file carrying the
account's private uploaded material each fail it, with the verdict and every leak check stored.
SYNTHETIC data only."""

from __future__ import annotations

import hashlib
import io
import json
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from documents.config import load_config
from documents.embed import HashingEmbedder
from documents.gate import PreDeliveryGate
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
CLEAN = (
    "Northwind Traders opened a new distribution centre in Ohio, according to its press release."
)


def docx(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for para in text.split("\n\n"):
        doc.add_paragraph(para)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _run(tenant: UUID, account: UUID, mode: str = "company") -> UUID:
    (row,) = admin(
        "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
        " reserved_credits, idempotency_key, input_mode) VALUES (%s, %s, 'test', '{}', 1, %s, %s)"
        " RETURNING id",
        tenant,
        account,
        f"k-{uuid4().hex[:10]}",
        mode,
    )
    admin(
        "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
        " credit_cap) VALUES (%s, %s, %s, 'full', 'queued', 'test', 1)",
        row[0],
        tenant,
        account,
    )
    return UUID(str(row[0]))


def _rows(tenant: UUID, run: UUID, unset: bool = False) -> None:
    proof = json.dumps({"call_refs": [], "calls": [], "notes": []})
    admin(
        "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, status, reason_code,"
        " proof, proof_hash) VALUES (%s, 'company_news', %s, 'high', 'done', NULL, %s, 'h'),"
        " (%s, 'podcasts', %s, 'medium', 'not_done', 'not_searched_file_only', %s, 'h')",
        run,
        tenant,
        proof,
        run,
        tenant,
        proof,
    )
    if unset:
        admin(
            "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority) VALUES"
            " (%s, 'youtube', %s, 'medium')",
            run,
            tenant,
        )


async def _deliver(
    store: MemoryPayloadStore,
    tenant: UUID,
    account: UUID,
    run: UUID,
    data: bytes,
    version: int,
    kind: str = "summary",
    manifest: dict[str, object] | None = None,
) -> None:
    sha = hashlib.sha256(data).hexdigest()
    key = f"{tenant}/{account}/{run}/deliverables/{sha}.docx"
    await store.put(key, data)
    h = "a" * 64
    admin(
        "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
        " filename, sha256, size_bytes, object_key, manifest_json, content_hash, snapshot_hash,"
        " theme_hash, rules_hash, built_at) VALUES (%s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, now())",
        tenant,
        account,
        run,
        h,
        kind,
        version,
        f"{kind}.docx",
        sha,
        len(data),
        key,
        json.dumps(manifest or {}),
        h,
        h,
        h,
        h,
    )


async def _deliver_set(
    store: MemoryPayloadStore, tenant: UUID, account: UUID, run: UUID, data: bytes
) -> None:
    """The five client files (the same clean body each; enough for the gate's checks)."""
    for kind in ("summary", "evidence", "people", "jobs", "tech"):
        await _deliver(store, tenant, account, run, data, 1, kind)


def _private_passage(tenant: UUID, account: UUID, text: str) -> None:
    (doc,) = admin(
        "INSERT INTO documents (tenant_id, account_id, name, declared_bytes, declared_type,"
        " quarantine_key, uploaded_by, upload_expires_at) VALUES (%s, %s, 'notes.docx', 10,"
        " 'docx', 'q', 'u', now()) RETURNING id",
        tenant,
        account,
    )
    sha = hashlib.sha256(text.encode()).hexdigest()
    admin(
        "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind, location,"
        " content, content_sha256, token_count)"
        " VALUES (%s, %s, %s, 1, 'section', '{}', %s, %s, 10)",
        tenant,
        account,
        doc[0],
        text,
        sha,
    )
    vec = HashingEmbedder().vector(text)
    admin(
        "INSERT INTO chunks (tenant_id, account_id, doc_id, content, embedding, passage_seq,"
        " embedding_model) VALUES (%s, %s, %s, %s, %s::halfvec, 1, 'local-hash-v1')",
        tenant,
        account,
        doc[0],
        text,
        "[" + ",".join(f"{v:.6f}" for v in vec) + "]",
    )


def _gate(store: MemoryPayloadStore) -> PreDeliveryGate:
    return PreDeliveryGate(tenant_txn, store, HashingEmbedder(), load_config())


async def test_gate_blocks_a_run_with_a_defect_and_passes_a_clean_one(tenant_a: UUID) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    _private_passage(
        tenant_a, account, f"Call notes. {fixtures.CANARY}. The budget renews in April."
    )
    clean = _run(tenant_a, account)
    _rows(tenant_a, clean)
    await _deliver_set(store, tenant_a, account, clean, docx(CLEAN))
    passed = await _gate(store).check(str(tenant_a), str(clean))
    assert not any(passed.values()), passed
    assert admin("SELECT verdict FROM run_gate_results WHERE run_id = %s", clean) == [("pass",)]
    leaks = admin(
        "SELECT verdict, passages_checked, hits FROM leak_checks WHERE run_id = %s", clean
    )
    assert len(leaks) == 5 and all(v == "pass" and n == 1 and h == [] for v, n, h in leaks)

    other = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    _private_passage(tenant_a, other, f"Call notes. {fixtures.CANARY}. The budget renews in April.")
    bad = _run(tenant_a, other)
    _rows(tenant_a, bad, unset=True)
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    # a broker name from config (no vendor is named in code); the denylist must catch it
    broker = sorted(n for n in vendor_names(load_providers_config()).values() if n != "Parallel")[0]
    text = (
        f"{CLEAN}\n\nThe pilot \u2014 if approved \u2014 starts soon. The search used {broker}"
        " and 40 credits.\n\n" + fixtures.CANARY + "."
    )
    await _deliver(store, tenant_a, other, bad, docx(text), 1)
    failing = await _gate(store).check(str(tenant_a), str(bad))
    assert failing["tracker_rows"] == ["youtube:unset"]
    assert failing["deliverable_set"] == ["evidence", "jobs", "people", "tech"]
    assert failing["client_dashes"] and failing["client_terms"] and failing["leak_check"]
    (verdict, defects) = admin(
        "SELECT verdict, defects FROM run_gate_results WHERE run_id = %s", bad
    )[0]
    assert verdict == "fail" and {d["check"] for d in defects} >= {
        "tracker_rows",
        "client_dashes",
        "client_terms",
        "leak_check",
    }
    # the stored records name codes and counts, never the private text
    stored = json.dumps(defects) + json.dumps(
        admin("SELECT hits FROM leak_checks WHERE run_id = %s", bad)
    )
    assert "Zebrafinch" not in stored and broker.lower() not in stored.lower()


async def test_file_mode_gate_requires_the_labelled_internal_file(tenant_a: UUID) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    run = _run(tenant_a, account, "file")
    _rows(tenant_a, run)
    missing = await _gate(store).check(str(tenant_a), str(run))
    assert missing["internal_label"] == ["missing"]
    admin(
        "INSERT INTO internal_deliverables (tenant_id, account_id, run_id, label, object_key,"
        " sha256, size_bytes) VALUES (%s, %s, %s, 'Internal: from your uploaded material', %s,"
        " %s, 1)",
        tenant_a,
        account,
        run,
        f"tenants/{tenant_a}/internal/{run}/{'b' * 64}.docx",
        "b" * 64,
    )
    assert not any((await _gate(store).check(str(tenant_a), str(run))).values())


async def test_gate_finds_a_lost_row_and_a_provider_host(tenant_a: UUID) -> None:
    from documents.gate import provider_hosts

    account = _make_account(tenant_a, f"rows-{uuid4().hex[:6]}.example")
    store = MemoryPayloadStore()
    run = _run(tenant_a, account)
    _rows(tenant_a, run)
    # the builder recorded a row the Word file does not hold, and a workbook names a host
    await _deliver(
        store, tenant_a, account, run, docx(CLEAN), 1, "summary", {"refs": {"summary": ["F-0001"]}}
    )
    for kind in ("evidence", "people", "jobs"):
        await _deliver(store, tenant_a, account, run, docx(CLEAN), 1, kind)
    await _deliver(
        store, tenant_a, account, run, docx(f"{CLEAN} See {provider_hosts()[0]}."), 1, "tech"
    )
    failing = await _gate(store).check(str(tenant_a), str(run))
    assert failing["rows_dropped"] == ["summary:1"]
    assert failing["third_party_domains"] == ["tech:1"]
