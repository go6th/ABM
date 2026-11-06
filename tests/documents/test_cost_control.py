"""Ingestion hardening PR6: the cost control of the upload analysis (step 5) and the embedding.

* a file-mode analysis run carries the token estimate of ``documents.estimate`` and pauses at the
  existing token guard (``token_gate``, the user's Proceed on ``/v1/runs/{id}/approve``);
* the analysis of a document starts at most once, whatever re-imports or retries follow;
* the document row holds its embedding tokens and the settled analysis tokens;
* the per-tenant model usage view (``llm_spend_daily``) shows both.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest

from account_intelligence.app import auto_analysis
from tests.connectors.conftest import Harness
from tests.documents import fixtures
from tests.documents.test_file_runs import (
    build,
    indexed_documents,
    process,
    put_document,
)
from tests.system.hermetic_stack import Stack, admin

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _automatic_starts_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the automatic starter itself (off unless the switch is on)."""
    monkeypatch.setenv("RESEARCH_AUTOMATIC_STARTS", "on")


def stack_with_reply_log(harness: Harness) -> Stack:
    """The hermetic stack with the model reply log on (as in production: the log is what the
    token guard, the token card and the usage view count; reuse across runs is off)."""
    from account_intelligence.app.container import reply_cache

    stack = build(harness)
    stack.plane.app.state.container.reply_cache = reply_cache({"RESEARCH_CACHE": "off"})
    return stack


def _analysis_runs(document: str) -> list[tuple[object, ...]]:
    return admin(
        "SELECT id, status, run_input FROM account_runs WHERE idempotency_key = %s",
        auto_analysis.idempotency_key(UUID(document)),
    )


def _queued(stack: Stack, run: str) -> int:
    scheduler = stack.plane.app.state.container.scheduler
    return sum(1 for _, r, _ in scheduler.queued if str(r) == run)


async def _bound_document(stack: Stack, who: object, account: str) -> str:
    doc = await put_document(who, "plan.docx", fixtures.notes_docx(), account=account)  # type: ignore[arg-type]
    await process(stack)
    return doc


async def test_the_analysis_run_carries_the_estimate_and_pauses_at_the_token_guard(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's ``expected_tokens`` is ``documents.estimate`` (input plus output); a run whose
    model tokens pass ``token_guard_multiple`` times it stops at ``token_gate`` and waits for the
    user, whose Proceed (``POST /v1/runs/{id}/approve``) lets it finish."""
    monkeypatch.delenv("RESEARCH_CAPS", raising=False)
    monkeypatch.delenv("DEV_UNCAPPED", raising=False)
    stack = stack_with_reply_log(harness)
    a = await stack.client("cost-guard")
    _, account = await indexed_documents(stack, a)
    doc = await _bound_document(stack, a, account)
    ((run_id, _, run_input),) = _analysis_runs(doc)
    run = str(run_id)
    expected = run_input["expected_tokens"]  # type: ignore[index]
    from documents.estimate import estimate

    assert isinstance(expected, int) and expected > 0
    # the same figure the screen shows before a file-mode run starts
    from account_intelligence.db.session import tenant_txn

    async with tenant_txn(a.tenant) as s:
        shown = await estimate(s, [UUID(doc)], False, 0.0)
    assert expected == shown["input_tokens"] + shown["output_tokens"]
    # a tiny estimate stands in for a run that passes its multiple after its first model call
    with psycopg.connect(ADMIN_DSN_OF(), autocommit=True) as c:
        c.execute(
            "UPDATE account_runs SET run_input = run_input || '{\"expected_tokens\": 1}'::jsonb"
            " WHERE id = %s",
            (run,),
        )
    scheduler = stack.plane.app.state.container.scheduler
    worker = _worker(stack)
    try:
        while scheduler.queued:
            tenant, run_id, slice_no = scheduler.queued.pop(0)
            reply = await worker.client.post(
                "/tasks/run-slice",
                json={"tenant_id": str(tenant), "run_id": str(run_id), "slice": slice_no},
            )
            assert reply.status_code == 200, reply.text
        status = admin("SELECT status, awaiting_kind FROM account_runs WHERE id = %s", run)
        assert status == [("AWAITING_APPROVAL", "cap")], status
        paused = admin("SELECT run_input->>'token_guard_ok' FROM account_runs WHERE id = %s", run)
        assert paused == [(None,)]  # nothing accepted yet
        proceed = await a.browser.call("POST", f"/v1/runs/{run}/approve")
        assert proceed.status_code == 200, proceed.text
        while scheduler.queued:
            tenant, run_id, slice_no = scheduler.queued.pop(0)
            reply = await worker.client.post(
                "/tasks/run-slice",
                json={"tenant_id": str(tenant), "run_id": str(run_id), "slice": slice_no},
            )
            assert reply.status_code == 200, reply.text
            if admin("SELECT status FROM account_runs WHERE id = %s", run) == [
                ("AWAITING_APPROVAL",)
            ]:
                # the guard moved three estimates past the accepted tokens; a still tiny estimate
                # may ask again, and the user goes on
                assert (await a.browser.call("POST", f"/v1/runs/{run}/approve")).status_code == 200
    finally:
        await worker.close()
    final = admin(
        "SELECT status, run_input->>'token_guard_ok' FROM account_runs WHERE id = %s", run
    )
    assert final[0][0] in ("COMPLETED", "PARTIAL"), final
    assert int(final[0][1]) > 0  # the user's Proceed recorded the tokens used so far
    await stack.close(a)


def ADMIN_DSN_OF() -> str:
    from tests.system.hermetic_stack import ADMIN_DSN

    return ADMIN_DSN


def _worker(stack: Stack) -> object:
    from tests.phase3.conftest import Browser

    return Browser(stack.plane)


async def test_the_analysis_of_a_document_starts_once_whatever_is_retried(
    harness: Harness,
) -> None:
    """``auto_analysis.start`` twice, the index task again, and an import again (the sweep's
    backfill after ``imported_at`` was cleared) create one run and queue one first slice."""
    from datetime import timedelta  # noqa: F401

    from documents.deliverables import backfill_imports

    stack = stack_with_reply_log(harness)
    a = await stack.client("cost-once")
    _, account = await indexed_documents(stack, a)
    doc = await _bound_document(stack, a, account)
    ((run_id, _, _),) = _analysis_runs(doc)
    run = str(run_id)
    container = stack.plane.app.state.container
    assert _queued(stack, run) == 1
    again = await auto_analysis.start(container, a.tenant, UUID(doc))
    assert again is not None and str(again) == run
    assert await auto_analysis.start(container, a.tenant, UUID(doc)) == again
    # the whole index step once more (a retried task): it replays the analysis, never repeats it
    row = await _document_row(container, a.tenant, UUID(doc))
    await container.documents._analyse(a.tenant, row)
    # a re-import: the sweep claims the document again and reads its rows
    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", doc)
    await backfill_imports(
        datetime.now(UTC),
        limit=10,
        payloads=container.documents.payloads,
        blobs=container.documents.blobs,
    )
    assert len(_analysis_runs(doc)) == 1
    assert _queued(stack, run) == 1
    assert admin(
        "SELECT count(*) FROM account_runs WHERE tenant_id = %s AND idempotency_key = %s",
        a.tenant,
        auto_analysis.idempotency_key(UUID(doc)),
    ) == [(1,)]
    await stack.close(a)


async def _document_row(container: object, tenant: UUID, document: UUID) -> object:
    from account_intelligence.db.session import tenant_txn
    from documents import repo

    async with tenant_txn(tenant) as s:
        return await repo.get(s, document)


async def test_embedding_is_batched_and_the_document_row_holds_the_tokens(
    harness: Harness,
) -> None:
    """``_index`` embeds ``cfg.embedding.batch`` passages per call; the document's
    ``embedding_tokens`` is the sum of its embedding call-log rows."""
    stack = stack_with_reply_log(harness)
    a = await stack.client("cost-embed")
    _, account = await indexed_documents(stack, a)
    service = stack.plane.app.state.container.documents
    cfg = service.cfg or service.c
    batch = 3
    service.cfg = cfg.model_copy(
        update={"embedding": cfg.embedding.model_copy(update={"batch": batch})}
    )
    sizes: list[int] = []
    real = service.embedder.embed

    async def spy(texts: list[str]) -> list[list[float]]:
        sizes.append(len(texts))
        return await real(texts)

    service.embedder.embed = spy
    long_text = "\n\n".join(
        f"Northwind Traders note {n}. " + "The platform team reviews its Salesforce roadmap. " * 30
        for n in range(12)
    )
    doc = await put_document(a, "long.txt", long_text.encode(), account=account)
    await process(stack)
    passages = admin("SELECT passages FROM documents WHERE id = %s", doc)[0][0]
    assert passages > batch, "the fixture must span more than one batch"
    assert sizes and max(sizes) == batch and all(n <= batch for n in sizes)
    assert sum(sizes) == passages
    assert len(sizes) == math.ceil(passages / batch)  # one page here: index_batch >= passages
    calls = admin(
        "SELECT count(*), coalesce(sum(input_tokens), 0) FROM research_file_model_calls"
        " WHERE file_id = %s AND task = 'embed_passages'",
        doc,
    )
    assert calls[0][0] == len(sizes) and calls[0][1] > 0
    assert admin("SELECT embedding_tokens FROM documents WHERE id = %s", doc) == [(calls[0][1],)]
    await stack.close(a)


async def test_settled_analysis_tokens_reach_the_document_and_the_usage_view(
    harness: Harness,
) -> None:
    """When the analysis run settles, its input and output tokens are set on the document and
    the run id is recorded; the per-tenant usage view (what the admin side reads) shows the
    analysis tokens (research surface) and the embedding tokens (file_embedding surface)."""
    stack = stack_with_reply_log(harness)
    a = await stack.client("cost-usage")
    _, account = await indexed_documents(stack, a)
    doc = await _bound_document(stack, a, account)
    ((run_id, _, _),) = _analysis_runs(doc)
    run = str(run_id)
    assert admin(
        "SELECT analysis_run_id, analysis_input_tokens FROM documents WHERE id = %s", doc
    ) == [(run_id, 0)]  # linked at start, no tokens before it settles
    await stack.drain(a, run)
    assert admin("SELECT status FROM account_runs WHERE id = %s", run)[0][0] in (
        "COMPLETED",
        "PARTIAL",
    )
    ((i, o, emb),) = admin(
        "SELECT analysis_input_tokens, analysis_output_tokens, embedding_tokens FROM documents"
        " WHERE id = %s",
        doc,
    )
    ((ri, ro),) = admin(
        "SELECT coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0)"
        " FROM model_reply_log WHERE run_id = %s AND served_from = 'model'",
        run,
    )
    assert i > 0 and o > 0 and (i, o) == (ri, ro)
    assert emb > 0
    # a redelivered settlement sets the same totals (set, not added)
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.runs import lifecycle

    async with tenant_txn(a.tenant) as s:
        await lifecycle._note_document_tokens(s, UUID(run), a.tenant)
    assert admin(
        "SELECT analysis_input_tokens, analysis_output_tokens FROM documents WHERE id = %s", doc
    ) == [(i, o)]
    usage = {
        (surface, kind): (int(inp), int(out), float(cost))
        for surface, kind, inp, out, cost in admin(
            "SELECT surface, task_kind, sum(input_tokens), sum(output_tokens), sum(cost_usd)"
            " FROM llm_spend_daily WHERE tenant_id = %s GROUP BY surface, task_kind",
            a.tenant,
        )
    }
    research_in = sum(v[0] for (surface, _), v in usage.items() if surface == "research")
    research_out = sum(v[1] for (surface, _), v in usage.items() if surface == "research")
    assert research_in >= i and research_out >= o
    embedded = usage[("file_embedding", "embed_passages")]
    assert embedded[0] > 0 and embedded[2] == 0.0
    # a client sees only its own usage: the view obeys row level security
    other = await stack.client("cost-usage-other")
    assert admin("SELECT count(*) FROM llm_spend_daily WHERE tenant_id = %s", other.tenant) == [
        (0,)
    ]
    await stack.close(a, other)
