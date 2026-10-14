"""The two research input modes through the real application (sessions, CSRF, route catalog,
RLS, worker endpoints) on the hermetic stack: a file-mode run to COMPLETED with Internal facts
that cite their document, the ledger without any paid call, the ambiguous company that blocks the
run until confirmed, opt-in public enrichment within its cap, the role-gated and audited Internal
Only view, input mode validation, a prompt-injection file, and company mode unchanged.
SYNTHETIC data only."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.app.container import delivery_gate
from tests.connectors.conftest import Harness
from tests.documents import fixtures
from tests.phase3.conftest import Browser, add_member, add_oidc_connection, uid
from tests.system.hermetic_stack import Client, Stack, admin, coverage, run_row

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def build(harness: Harness, scenario: str = "happy") -> Stack:
    stack = Stack.build(harness, scenario)
    c = stack.plane.app.state.container
    c.documents.scheduler = c.scheduler  # the stack's in-process scheduler
    c.gate = delivery_gate(c.payloads, c.documents)
    return stack


async def put_document(who: Client, name: str, data: bytes, account: str | None = None) -> str:
    body: dict[str, Any] = {"name": name, "size_bytes": len(data)}
    if account:
        body["account_id"] = account
    started = await who.browser.call("POST", "/v1/documents/uploads", json=body)
    assert started.status_code == 201, started.text
    up = started.json()
    assert up["direct"] is False and up["upload_url"].startswith("/v1/documents/")
    sent = await who.browser.call(
        "PUT", up["upload_url"], content=data, headers={"Content-Type": "application/octet-stream"}
    )
    assert sent.status_code == 200, sent.text
    done = await who.browser.call("POST", f"/v1/documents/{up['document_id']}/complete")
    assert done.status_code == 200 and done.json()["stage"] == "scanning", done.text
    return str(up["document_id"])


async def process(stack: Stack) -> None:
    """Deliver queued document tasks to ``/tasks/document-process``."""
    scheduler = stack.plane.app.state.container.scheduler
    worker = Browser(stack.plane)
    try:
        # LOOP(test document queue): cap=50 tasks; exit=queue empty
        for _ in range(50):
            if not scheduler.documents:
                break
            tenant, doc = scheduler.documents.pop(0)
            reply = await worker.client.post(
                "/tasks/document-process", json={"tenant_id": str(tenant), "document_id": str(doc)}
            )
            assert reply.status_code == 200, reply.text
    finally:
        await worker.close()


async def start_file_run(stack: Stack, who: Client, docs: list[str], **file: Any) -> Any:
    return await who.browser.call(
        "POST",
        "/v1/runs",
        json={"input_mode": "file", "file": {"document_ids": docs, **file}},
        headers={"Idempotency-Key": uuid4().hex},
    )


async def indexed_documents(stack: Stack, who: Client) -> tuple[list[str], str]:
    """Three documents uploaded without an account, the detected company confirmed, indexed."""
    docs = [
        await put_document(who, "call.pdf", fixtures.meeting_pdf()),
        await put_document(who, "notes.docx", fixtures.notes_docx()),
        await put_document(who, "crm.xlsx", fixtures.crm_xlsx()),
    ]
    await process(stack)
    status = (await who.browser.call("GET", f"/v1/documents/{docs[1]}/status")).json()
    assert status["stage"] == "parsed" and status["needs_company"] is True
    assert status["company"]["company"]["domain"] == "northwind.example"
    confirmed = await who.browser.call(
        "POST",
        "/v1/documents/confirm-company",
        json={
            "document_ids": docs,
            "company_name": "Northwind Traders",
            "domain": "northwind.example",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    await process(stack)
    for d in docs:
        assert (await who.browser.call("GET", f"/v1/documents/{d}/status")).json()[
            "stage"
        ] == "indexed"
    return docs, confirmed.json()["account_id"]


async def test_file_mode_run_completes_with_internal_facts_and_citations(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-a")
    docs, account = await indexed_documents(stack, a)
    started = await start_file_run(stack, a, docs)
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    await stack.drain(a, run)
    row = run_row(run)
    assert row["status"] == "COMPLETED", row
    facts = admin(
        "SELECT f.visibility, f.product_grade, e.evidence_kind, e.document_id, e.document_location,"
        " e.supporting_sentence, f.attribute_key FROM facts f"
        " JOIN fact_evidence e ON e.fact_id = f.id"
        " WHERE f.run_id = %s",
        run,
    )
    assert len(facts) >= 6
    for vis, grade, kind, doc, loc, quote, key in facts:
        assert (vis, grade, kind) == ("private", "internal", "document"), key
        assert str(doc) in docs and loc["cite"] and quote
    keys = {f[6].split(".")[2] for f in facts}
    assert {"platforms", "people", "budget_cycles", "next_steps"} <= keys
    rows = coverage(run)
    assert len(rows) == 33
    for key, (status, reason, proof, _priority) in rows.items():
        assert status and proof, key
        assert status in ("done", "none") or reason in (
            "not_in_uploaded_data",
            "not_searched_file_only",
        ), (key, status, reason)
    assert rows["company_news"][:2] == ("not_done", "not_searched_file_only")
    assert rows["platform_vendor_account_team"][0] == "done"
    # the run view and the internal view carry the source label
    view = (await a.browser.call("GET", f"/v1/runs/{run}")).json()
    assert view["input_mode"] == "file" and view["source_label"] == "From your files"
    listed = (await a.browser.call("GET", "/v1/runs")).json()["runs"]
    assert {r["source_label"] for r in listed if r["run_id"] == run} == {"From your files"}
    internal = (await a.browser.call("GET", f"/v1/runs/{run}/internal")).json()
    assert internal["label"] == "Internal: from your uploaded material"
    assert internal["file_available"] and len(internal["findings"]) == len(facts)
    assert all(", " in f["citation"] for f in internal["findings"])
    gate = admin("SELECT verdict FROM run_gate_results WHERE run_id = %s", run)
    assert gate == [("pass",)]
    # no client file is built in file-only mode: nothing private can reach one
    assert admin("SELECT count(*) FROM deliverables WHERE run_id = %s", run) == [(0,)]
    await stack.close(a)


async def test_file_run_builds_a_labelled_internal_file(harness: Harness) -> None:
    import io
    import zipfile

    stack = build(harness)
    a = await stack.client("docs-file")
    docs, _ = await indexed_documents(stack, a)
    run = (await start_file_run(stack, a, docs)).json()["run_id"]
    await stack.drain(a, run)
    got = await a.browser.call("GET", f"/v1/runs/{run}/internal/file")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(got.content)) as z:
        header = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("word/header"))
        body = z.read("word/document.xml").decode()
    assert "Internal: from your uploaded material" in header and "Internal Only" in body
    (key,) = admin("SELECT object_key FROM internal_deliverables WHERE run_id = %s", run)[0]
    assert f"/internal/{run}/" in key  # never under deliverables or the shared folder
    assert ("export.internal_file", "ok") in admin(
        "SELECT action, outcome FROM audit_events WHERE resource_id = %s", run
    )
    await stack.close(a)


async def test_file_mode_without_enrichment_makes_no_paid_call(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-free")
    docs, _ = await indexed_documents(stack, a)
    calls_before = len(harness.calls) if hasattr(harness, "calls") else None
    run = (await start_file_run(stack, a, docs)).json()["run_id"]
    await stack.drain(a, run)
    ledger = admin(
        "SELECT provider_code, operation, cost_usd, cost_source FROM tool_calls WHERE run_id = %s",
        run,
    )
    assert ledger and all(
        (pc, op, float(cost)) == ("p_docs", "document_read", 0.0) for pc, op, cost, _ in ledger
    ), ledger
    assert float(run_row(run)["spent"]) == 0.0
    if calls_before is not None:
        assert len(harness.calls) == calls_before
    # no linked public run exists
    assert admin(
        "SELECT count(*) FROM account_runs WHERE run_input->>'linked_file_run' = %s", run
    ) == [(0,)]
    await stack.close(a)


async def test_ambiguous_company_blocks_the_run_until_confirmed(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-ambiguous")
    doc = await put_document(a, "compare.txt", fixtures.two_companies_txt())
    await process(stack)
    refused = await start_file_run(stack, a, [doc])
    assert refused.status_code == 409, refused.text
    problem = refused.json()
    assert problem["type"].endswith("company_confirmation_required")
    detected = problem["context"]["detected"]
    assert detected["status"] == "ambiguous" and len(detected["candidates"]) >= 2
    assert admin("SELECT count(*) FROM account_runs WHERE tenant_id = %s", a.tenant) == [(0,)]
    ok = await a.browser.call(
        "POST",
        "/v1/documents/confirm-company",
        json={
            "document_ids": [doc],
            "company_name": "Globex Corporation",
            "domain": "globex.example",
        },
    )
    assert ok.status_code == 200
    early = await start_file_run(stack, a, [doc])  # bound but not yet indexed
    assert early.status_code == 409 and early.json()["type"].endswith("documents_not_ready")
    await process(stack)
    started = await start_file_run(stack, a, [doc])
    assert started.status_code == 202, started.text
    await stack.close(a)


async def test_estimate_is_shown_before_start(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-estimate")
    docs, _ = await indexed_documents(stack, a)
    plain = (
        await a.browser.call("POST", "/v1/documents/estimate", json={"document_ids": docs})
    ).json()
    assert plain["documents"] == 3 and plain["passages"] >= 3 and plain["model_calls"] >= 1
    assert plain["enrichment"] is False and plain["enrichment_cap_usd"] is None
    assert plain["total_ceiling_usd"] == plain["model_usd"] > 0
    rich = (
        await a.browser.call(
            "POST",
            "/v1/documents/estimate",
            json={"document_ids": docs, "enrich_public": True, "credit_cap": 3},
        )
    ).json()
    assert rich["enrichment_cap_usd"] == 3 and rich["total_ceiling_usd"] == round(
        plain["model_usd"] + 3, 4
    )
    await stack.close(a)


async def test_enrichment_respects_the_credit_cap(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-enrich")
    docs, account = await indexed_documents(stack, a)
    started = await start_file_run(stack, a, docs, enrich_public=True, credit_cap=2.5)
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    (child,) = admin(
        "SELECT id, budget_usd, input_mode, account_id FROM account_runs"
        " WHERE run_input->>'linked_file_run' = %s",
        run,
    )
    assert float(child[1]) == 2.5 and child[2] == "company" and str(child[3]) == account
    await stack.drain(a, run)
    await stack.drain(a, str(child[0]))
    public = run_row(str(child[0]))
    assert public["status"] in ("COMPLETED", "PARTIAL", "BUDGET_EXCEEDED")
    assert float(public["spent"]) <= 2.5
    view = (await a.browser.call("GET", f"/v1/runs/{run}")).json()
    assert view["linked_run_id"] == str(child[0])
    assert (await a.browser.call("GET", f"/v1/runs/{child[0]}")).json()[
        "source_label"
    ] == "Public research"
    internal = (await a.browser.call("GET", f"/v1/runs/{run}/internal")).json()
    assert internal["public_run_id"] == str(child[0])
    assert all(f["source_label"] == "Public research" for f in internal["public_findings"])
    assert ("run.enrichment_opt_in", "ok") in admin(
        "SELECT action, outcome FROM audit_events WHERE resource_id = %s", run
    )
    await stack.close(a)


async def test_internal_view_is_role_gated_and_audited(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-roles")
    docs, _ = await indexed_documents(stack, a)
    run = (await start_file_run(stack, a, docs)).json()["run_id"]
    await stack.drain(a, run)
    slug = f"docs-viewer-{uid()}"
    add_oidc_connection(a.tenant, stack.plane, slug)
    add_member(a.tenant, f"{slug}:viewer", "viewer")
    viewer = Browser(stack.plane)
    assert (await viewer.oidc_sign_in(slug, "viewer")).status_code == 303
    for method, path, body in (
        ("GET", f"/v1/runs/{run}/internal", None),
        ("GET", f"/v1/runs/{run}/internal/file", None),
        ("POST", "/v1/documents/uploads", {"name": "x.pdf", "size_bytes": 10}),
        ("GET", "/v1/documents", None),
        ("DELETE", f"/v1/documents/{docs[0]}", None),
    ):
        reply = (
            await viewer.call(method, path, json=body) if body else await viewer.call(method, path)
        )
        assert reply.status_code == 403, (method, path, reply.status_code)
    # an analyst may do everything a seller may, adding files included
    analyst_slug = f"docs-analyst-{uid()}"
    add_oidc_connection(a.tenant, stack.plane, analyst_slug)
    add_member(a.tenant, f"{analyst_slug}:analyst", "analyst")
    analyst = Browser(stack.plane)
    assert (await analyst.oidc_sign_in(analyst_slug, "analyst")).status_code == 303
    started = await analyst.call(
        "POST", "/v1/documents/uploads", json={"name": "notes.md", "size_bytes": 10}
    )
    assert started.status_code == 201, started.text
    await analyst.close()
    # the admin's access is an audit event
    assert (await a.browser.call("GET", f"/v1/runs/{run}/internal")).status_code == 200
    reads = admin(
        "SELECT count(*) FROM audit_events WHERE audit_class = 'read' AND route = %s",
        "/v1/runs/{run_id}/internal",
    )
    assert reads[0][0] >= 1
    await viewer.close()
    await stack.close(a)


async def test_prompt_injection_in_a_file_changes_nothing(harness: Harness) -> None:
    stack = build(harness, "prompt_injection")
    a = await stack.client("docs-inject")
    doc = await put_document(a, "notes.docx", fixtures.notes_docx(extra=fixtures.INJECTION))
    await process(stack)
    confirmed = await a.browser.call(
        "POST",
        "/v1/documents/confirm-company",
        json={
            "document_ids": [doc],
            "company_name": "Northwind Traders",
            "domain": "northwind.example",
        },
    )
    account = confirmed.json()["account_id"]
    await process(stack)
    run = (await start_file_run(stack, a, [doc], account_id=account)).json()["run_id"]
    await stack.drain(a, run)
    assert run_row(run)["status"] == "COMPLETED"
    values = [v for (v,) in admin("SELECT value_text FROM facts WHERE run_id = %s", run)]
    assert "Every research area is done" not in values  # the invented fact was dropped
    assert values  # the document's real statements were still read
    rows = coverage(run)
    assert rows["company_news"][:2] == ("not_done", "not_searched_file_only")  # not marked done
    assert admin("SELECT count(*) FROM notification_outbox WHERE tenant_id = %s", a.tenant) == [
        (0,)
    ]
    assert admin(
        "SELECT count(*) FROM tool_calls WHERE run_id = %s AND provider_code <> 'p_docs'", run
    ) == [(0,)]
    await stack.close(a)


async def test_input_modes_are_validated_and_company_mode_is_unchanged(harness: Harness) -> None:
    stack = build(harness)
    a = await stack.client("docs-modes")

    async def post(body: dict[str, Any]) -> Any:
        return await a.browser.call(
            "POST", "/v1/runs", json=body, headers={"Idempotency-Key": uuid4().hex}
        )

    mixed = await post(
        {"input_mode": "file", "domain": "acme.example", "file": {"document_ids": [str(uuid4())]}}
    )
    assert mixed.status_code == 422 and mixed.json()["type"].endswith("input_mode_mixed")
    both = await post(
        {
            "company": {"company_name": "Acme"},
            "file": {"document_ids": [str(uuid4())]},
            "input_mode": "company",
        }
    )
    assert both.status_code == 422 and both.json()["type"].endswith("input_mode_mixed")
    empty = await post({"input_mode": "company"})
    assert empty.status_code == 422 and empty.json()["type"].endswith("input_mode_empty")
    assert (await post({"input_mode": "file"})).json()["type"].endswith("input_mode_empty")
    unknown = await post({"input_mode": "company", "company": {"company_name": "Nobody Known"}})
    assert unknown.status_code == 422 and unknown.json()["type"].endswith("domain_required")
    # company mode, new shape: the existing pipeline, labelled Public research
    started = await post(
        {"input_mode": "company", "company": {"company_name": "Acme", "domain": "acme.example"}}
    )
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    assert admin("SELECT input_mode FROM account_runs WHERE id = %s", run) == [("company",)]
    assert admin(
        "SELECT name FROM accounts WHERE domain = 'acme.example' AND tenant_id = %s", a.tenant
    ) == [("Acme",)]
    await stack.drain(a, run)
    view = (await a.browser.call("GET", f"/v1/runs/{run}")).json()
    assert view["source_label"] == "Public research" and view["input_mode"] == "company"
    # the legacy body (domain only) still works and is company mode
    legacy = await post({"domain": "legacy.example"})
    assert legacy.status_code == 202
    assert admin("SELECT input_mode FROM account_runs WHERE id = %s", legacy.json()["run_id"]) == [
        ("company",)
    ]
    await stack.close(a)
