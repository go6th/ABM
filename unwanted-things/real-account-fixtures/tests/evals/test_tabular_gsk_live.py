"""GAP 2/3 of the spreadsheet-parity follow-up (2026-10-17): the five questions of
``evals/tabular_gsk/cases.yaml`` asked of the REAL model over the owner's REAL GSK workspace,
end to end — the router decides, the table tools run on Postgres, the streamed answer is what
is judged. The model comes from the one layer every surface reads: ``surfaces.eval`` of
``config/models.yaml`` (default kimi-k3 at effort high), overridable per run with
``EVAL_MODEL`` / ``EVAL_EFFORT`` — Kimi K3, Claude Fable 5.1 or Claude Opus 5.5. Each case runs
five times: the five canonical tool-call
traces (``_canon``: only the evidence calls — the FILTERED ``query_table`` calls and the
``read_rows`` calls on the case's ``target_table``, with the projection, the order-insensitive
lists and an ``icontains`` value's case folded away as provably result-preserving; describe,
other-table and where-less calls are exploration, not evidence) must CONVERGE — at least three
of the five identical (a batched cloud model is not bit-identical even at temperature 0 with a
pinned seed; the brief's "identical tool traces" landing as one converged evidence sequence),
and every run's answer — deviants included — must pass the case's assertions. Marked ``live``:
the nightly eval workflow and on-demand runs only, never the
offline suite; the run FAILS without the eval model's key (MOONSHOT_API_KEY for Kimi K3,
ANTHROPIC_API_KEY for a Claude model; silence would hide a broken harness).

The stubbed counterpart (no spend, same pipeline shape) is
``tests/documents/test_tables_golden.py``; the store's truth against the same file is
``tests/documents/test_tables_real_gsk.py``.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml

from account_intelligence.chat.registry import get_chat_registry
from account_intelligence.chat.service import AnthropicChatProvider, MoonshotChatProvider
from account_intelligence.config import get_settings
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import answers as A
from account_intelligence.replica import router as R
from account_intelligence.replica.model_answer import stream_with_model
from account_intelligence.replica.tools import AccessTools
from documents import tables as T
from tests.conftest import _make_account
from tests.documents.world import service, upload

pytestmark = [pytest.mark.live, pytest.mark.requires_postgres, pytest.mark.asyncio]

ZIP = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "gsk" / "GSK_workspace.zip"
SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "evals" / "tabular_gsk" / "cases.yaml").read_text()
)
CASES = SPEC["cases"]
RUNS = 5  # the determinism bar: five runs of a question …
MODAL = 3  # … three of which must share ONE canonical tool trace (see ``_canon``)


def _matches(text: str, needle: str) -> bool:
    """One assertion term: ``re:`` a case-insensitive regex, ``Re:`` a case-sensitive one,
    anything else a case-insensitive substring."""
    if needle.startswith("re:"):
        return bool(re.search(needle[3:], text, re.I))
    if needle.startswith("Re:"):
        return bool(re.search(needle[3:], text))
    return needle.casefold() in text.casefold()


def _model_and_provider() -> tuple[Any, Any]:
    """The eval's model and provider from the one layer every surface reads (owner decision
    2026-10-17): the ``surfaces.eval`` entry of ``config/models.yaml``, overridable per run
    with ``EVAL_MODEL`` / ``EVAL_EFFORT`` (the default is kimi-k3 at high — the landed suite).
    Kimi K3 streams from Moonshot, where temperature 0 also pins the seed; a Claude model
    streams from Anthropic, whose Messages call has no temperature or seed parameter — there
    the per-run answer assertions are the hard gate and the convergence bar the tolerated
    signal (a batched cloud model is not bit-identical either way)."""
    from account_intelligence.platform.llm import resolve_eval_choice
    from llm.choice import catalogue

    target = resolve_eval_choice()
    model = get_chat_registry().resolve(target.model)
    effort = target.effort or "high"
    found = catalogue().get(target.model)
    if found is not None and found.provider == "moonshot":
        settings = get_settings()
        key = settings.moonshot_api_key.get_secret_value() if settings.moonshot_api_key else ""
        if not key and settings.moonshot_api_key_file:
            key = Path(settings.moonshot_api_key_file).read_text().strip()
        if not key:
            import dotenv

            key = (
                os.environ.get("MOONSHOT_API_KEY")
                or dotenv.get_key(Path(__file__).resolve().parents[2] / ".env", "MOONSHOT_API_KEY")
                or ""
            )
        if not key:
            pytest.fail("the live tabular eval needs MOONSHOT_API_KEY (settings or environment)")
        from llm.adapters.moonshot_api import BASE_URL, MoonshotClient

        client = MoonshotClient(
            key,
            base_url=os.environ.get("MOONSHOT_BASE_URL") or BASE_URL,
            dialect=os.environ.get("MOONSHOT_API_DIALECT") or "moonshot",
            remote_model=os.environ.get("MOONSHOT_MODEL") or None,
        )
        return model, MoonshotChatProvider(client, effort=effort)
    settings = get_settings()
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not key:
        import dotenv

        key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or dotenv.get_key(Path(__file__).resolve().parents[2] / ".env", "ANTHROPIC_API_KEY")
            or ""
        )
    if not key:
        pytest.fail("the live tabular eval on a Claude model needs ANTHROPIC_API_KEY")
    from account_intelligence.platform.llm import anthropic_api_client

    return model, AnthropicChatProvider(anthropic_api_client(key), effort=effort)


EVIDENCE_CALLS = ("query_table", "read_rows")  # the calls a canon judges (owner call 2026-10-17)


def _canon(trace: list[dict[str, Any]], target: str) -> str:
    """One trace's canonical form for the determinism check: ONLY the evidence calls — the
    FILTERED ``query_table`` calls and the ``read_rows`` calls on the case's target table.
    Calls that decide nothing about the answer's evidence are no part of a canon:
    ``describe_table``, calls on other tables, and a where-less ``query_table`` (a row-count or
    a distinct scan is schema exploration, like describe). Each evidence call is its name plus
    payload with everything PROVABLY result-preserving folded away: the projection (which
    columns a call returns changes no rows), the orders of a where conjunction, of an ``in``
    list and of a read's row list (all match as sets), an order_by entry's omitted desc (the
    store default is ascending) and the case of an ``icontains`` value (the store matches it
    lowered, SQL-side). What was asked for is judged by the case's answer assertions — "not the
    same text"."""
    parts = []
    for call in trace:
        if call["name"] not in EVIDENCE_CALLS:
            continue
        payload = json.loads(json.dumps(call["input"], sort_keys=True, default=str))
        if target not in str(payload.get("table") or ""):
            continue
        if call["name"] == "query_table" and not payload.get("where"):
            continue  # no predicate, no evidence decision: exploration, like describe_table
        payload.pop("columns", None)
        for order in payload.get("order_by") or []:
            order.setdefault("desc", False)  # the store's default: omitted means ascending
        for predicate in payload.get("where") or []:
            if predicate.get("op") == "icontains" and isinstance(predicate.get("value"), str):
                predicate["value"] = predicate["value"].casefold()
            if predicate.get("op") == "in" and isinstance(predicate.get("value"), list):
                predicate["value"] = sorted(
                    predicate["value"], key=lambda item: json.dumps(item, sort_keys=True)
                )
        for key in ("where", "rows"):
            items = payload.get(key)
            if isinstance(items, list):
                payload[key] = sorted(
                    items, key=lambda item: json.dumps(item, sort_keys=True, default=str)
                )
        parts.append(f"{call['name']} {json.dumps(payload, sort_keys=True)}")
    return "\n".join(parts)


async def _ask(
    tenant: UUID, account: UUID, acct: A.Acct, model: Any, provider: Any, question: str
) -> tuple[list[dict[str, Any]], str, str]:
    """One end-to-end turn (router → tools → streamed answer): the tool trace (name plus the
    payload the model sent, plus the row count a query answered with), the answer's text and
    the route taken."""
    ws = A.Workspace([acct], [], [], [], [], [], [], {}, False)
    async with tenant_txn(tenant, user="user-1") as s:
        found = await T.list_tables(s, [account])
    decision = await R.route(question, found)
    assert decision.route in ("tabular", "mixed"), decision
    ws.tables = A.table_rows(ws, found)
    tools = AccessTools(lambda: tenant_txn(tenant, user="user-1"), found, [account])
    trace: list[dict[str, Any]] = []

    async def run_tool(name: str, payload: dict[str, Any]) -> Any:
        out = await tools.run(name, payload)
        entry: dict[str, Any] = {
            "name": name,
            "input": json.loads(json.dumps(payload, sort_keys=True, default=str)),
        }
        if isinstance(out, dict) and "row_count" in out:
            entry["row_count"] = out["row_count"]
        trace.append(entry)
        return out

    q, local = A.draft_local(question, ws, datetime.now(UTC))
    paras: dict[int, str] = {}
    usable = False
    async for kind, value in stream_with_model(
        provider,
        model,
        q,
        ws,
        local,
        user_id="eval",
        request_id=str(uuid4()),
        tools=tools.offered(decision.route),
        run_tool=run_tool,
        temperature=0.0,
    ):
        if kind == "piece":
            index, piece = value
            paras[index] = f"{paras.get(index, '')}{piece}"
        elif kind == "discard":  # text before a tool call is withdrawn, as the client does
            paras = {}
        elif kind == "done":
            usable = True
    text = "\n\n".join(paras[i] for i in sorted(paras))
    assert usable and text.strip(), "the model's answer could not be used (citation check)"
    return trace, text, decision.route


def _check_case(case: dict[str, Any], trace: list[dict[str, Any]], text: str) -> None:
    called = [c["name"] for c in trace]
    for name in case.get("must_call", []):
        assert name in called, f"{name} never called: {called}"
    target = case["target_table"]  # a wrong-table run fails here — it may never luck out
    assert any(
        c["name"] == "query_table" and target in str(c["input"].get("table") or "") for c in trace
    ), f"no query_table call on the target table {target}: {trace}"
    for want in case.get("predicates", []):
        assert any(
            c["name"] == "query_table"
            and any(
                p.get("column") == want["column"]
                and p.get("op") == want["op"]
                and want["value_contains"].casefold() in str(p.get("value", "")).casefold()
                for p in (c["input"].get("where") or [])
            )
            for c in trace
        ), f"predicate {want} absent from {trace}"
    counts = [c.get("row_count") for c in trace if c["name"] == "query_table"]
    for rows in case.get("query_row_counts", []):
        assert rows in counts, f"no query returned {rows} rows: {counts}"
    for group in case.get("answer_any", []):
        assert any(_matches(text, needle) for needle in group), f"none of {group} in the answer"
    for rid in case.get("answer_ids", []):
        assert _matches(text, rf"re:\b{re.escape(rid)}\b"), f"{rid} not in the answer"
    for bad in SPEC["forbidden_anywhere"]:
        assert not _matches(text, bad), f"the answer refuses or redirects: {bad!r}"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_tabular_gsk_live(tenant_a: UUID, tmp_path: Path, case: dict[str, Any]) -> None:
    assert ZIP.exists(), f"the real GSK workspace fixture is missing: {ZIP}"
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    acct = A.Acct(account, f"gsk-{uuid4().hex[:6]}.example", "GSK", [], None, None, 1)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, ZIP.name, ZIP.read_bytes(), account)
    assert await svc.process(tenant_a, UUID(done["document_id"])) == "indexed"
    model, provider = _model_and_provider()
    runs = [
        await _ask(tenant_a, account, acct, model, provider, case["question"]) for _ in range(RUNS)
    ]
    canons = [_canon(trace, case["target_table"]) for trace, _text, _route in runs]
    counts = Counter(canons)
    modal, n = counts.most_common(1)[0]
    assert n >= MODAL, (
        f"the evidence-call sequences did not converge ({MODAL}/{RUNS} needed): "
        + ", ".join(f"{count} run(s)" for count in sorted(counts.values(), reverse=True))
        + "\nthe modal canon:\n"
        + modal
        + "\none deviant canon:\n"
        + next(c for c in canons if c != modal)
    )
    for i, (trace, text, _route) in enumerate(runs):
        try:
            _check_case(case, trace, text)
        except AssertionError as error:
            raise AssertionError(f"{case['name']} run {i}: {error}\nanswer was:\n{text}") from None
