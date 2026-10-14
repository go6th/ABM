"""The file-mode run (input mode "file") as one LangGraph graph on the runner's checkpointer.

::

    setup -> extract -> tracker -> internal_file -> gate -> deliver -> finish

* ``setup``: the run's documents must all be indexed and bound to the run's account.
* ``extract``: facts from the run's passages (``documents.extract``: rows by code, prose by the
  model behind the untrusted-content boundary), recorded through the grading write path as
  private facts graded Internal (Confirmed only from an authoritative document), each citing
  its document and passage. Model replies are stored and replay on a resumed slice; facts are
  idempotent per content.
* ``tracker``: all 33 rows. A row the uploads can answer is proven by a zero-cost document-read
  ledger record carrying the number of facts found; a row without such facts is Not done
  ``not_in_uploaded_data`` (``source_unavailable`` when the model could not read the prose);
  a row that needs public sources is Not done ``not_searched_file_only``.
* ``internal_file``: the Internal Only Word file, labelled "Internal: from your uploaded
  material", stored apart from client files.
* ``gate``: the pre-delivery gate (``documents.gate``); a failure ends the run PARTIAL
  ``gate_failed`` with the defects stored for the operator.
* ``finish``: the single settlement point (the runner settles once at the terminal state).

No node calls a data broker: a file-mode run's ledger holds document reads only (cost 0). The
optional public enrichment is a separate, linked company-mode run with its own cap approval.

GUARANTEE(test_file_mode_run_completes_with_internal_facts_and_citations)
GUARANTEE(test_file_mode_without_enrichment_makes_no_paid_call)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import text

from account_intelligence.workflow.state import merge_maps, sum_counters
from account_intelligence.workflow.transitions import RunStatus
from documents import extract as ex
from documents import repo
from documents.config import InputModesConfig, load_config

SETUP_FAILURES = frozenset({"no_documents", "documents_not_ready"})


class FileState(TypedDict, total=False):
    """Checkpoint state: ids, flags and counters only (no document text)."""

    tenant_id: str
    run_id: str
    account_id: str
    account_name: str
    domain: str
    enrichment_run_id: str | None
    outcome: str
    error_code: str | None
    model_error: str | None
    settled: bool
    counters: Annotated[dict[str, int], sum_counters]
    exits: Annotated[dict[str, str], merge_maps]


PASSAGE_PAGE = 2000  # passages read and extracted per page (a file of any size)


def _model_failure(error: BaseException) -> str | None:
    from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured
    from llm.errors import (
        AdapterNotConfigured,
        AuthFailed,
        ContentRefused,
        DataClassRefused,
        ModelUnavailable,
        ProviderCreditsExhausted,
    )

    for cls, code in (
        (ProviderCreditsExhausted, "model_credits_exhausted"),
        (AuthFailed, "model_auth_failed"),
        (ModelUnavailable, "model_unavailable"),
        (AdapterNotConfigured, "model_not_configured"),
        (PlatformNotConfigured, "model_not_configured"),
        (ContentRefused, "model_refused"),
        (DataClassRefused, "model_data_class_refused"),
    ):
        if isinstance(error, cls):
            return code
    return None


def build_file_graph(deps: Any, documents: Any, cfg: InputModesConfig | None = None) -> Any:
    """The uncompiled graph (see the module docstring)."""
    cfg = cfg or load_config()

    def ids(state: FileState) -> tuple[UUID, UUID, UUID]:
        return UUID(state["tenant_id"]), UUID(state["run_id"]), UUID(state["account_id"])

    async def setup(state: FileState) -> dict[str, Any]:
        """Every document of the run is indexed and bound to the run's account."""
        tenant, run, account = ids(state)
        async with deps.txn(tenant) as s:
            docs = await repo.run_documents(s, run)
        if not docs:
            return {"outcome": "failed", "error_code": "no_documents"}
        if any(d.status != "indexed" or d.account_id != account for d in docs):
            return {"outcome": "failed", "error_code": "documents_not_ready"}
        return {"outcome": "running", "exits": {"setup": "documents_ready"}}

    async def model_call(task_kind: str, system: str, user: str) -> Any:
        """The run's recorded model call, marked private data."""
        return await deps.model(task_kind, system, user, data_class="private")

    async def extract(state: FileState) -> dict[str, Any]:
        """Facts from the passages, recorded as private Internal facts with citations."""
        from account_intelligence.grading.config import load_grading_config
        from account_intelligence.grading.ports import RunInfo
        from account_intelligence.grading.repo import record_document_fact
        from documents.chunking import cite

        tenant, run, account = ids(state)
        async with deps.txn(tenant) as s:
            documents = await repo.run_documents(s, run)
        drops: Counter[str] = Counter()
        model_error = None
        company = f"{state['account_name']} ({state['domain']})"
        info = RunInfo(
            tenant, account, run, deps.clock.now(), state["domain"], state["account_name"]
        )
        config_hash = load_grading_config().content_hash
        seen: set[tuple[str, str]] = set()
        facts = written = passages = 0
        # a file of any size: each document is read a page of passages at a time (owner decision
        # 2026-10-14); facts are written page by page and de-duplicated across pages
        # LOOP(file extraction documents): cap=the run's documents; exit=last document
        for doc in documents:
            after = -1
            # LOOP(file extraction pages): cap=passages / PASSAGE_PAGE; exit=a short page
            while True:
                async with deps.txn(tenant) as s:
                    rows = await repo.document_passages_page(s, doc.id, after, PASSAGE_PAGE)
                if not rows:
                    break
                after, passages = int(rows[-1].seq), passages + len(rows)
                cands = [c for r in rows if r.kind == "crm_row" for c in ex.from_row(r)]
                prose = [r for r in rows if r.kind != "crm_row"]
                if prose and model_error is None:
                    try:
                        found, more = await ex.from_prose(model_call, company, prose, cfg)
                        cands += found
                        drops.update(more)
                    except Exception as error:  # noqa: BLE001 - re-raised unless terminal
                        model_error = _model_failure(error)
                        if model_error is None:
                            raise
                cands = ex.dedupe(cands, seen)
                facts += len(cands)
                async with deps.txn(tenant) as s:
                    for c in cands:
                        location = {**c.location, "passage": c.seq, "cite": cite(c.location)}
                        _, created = await record_document_fact(
                            s,
                            run=info,
                            attribute_key=ex.attribute_key(cfg, c),
                            value=c.value,
                            quote=c.quote,
                            document_id=c.document_id,
                            location=location,
                            authoritative=c.authoritative,
                            flags=(f"reader_{c.reader}", f"category_{c.category}"),
                            config_hash=config_hash,
                        )
                        written += int(created)
                if len(rows) < PASSAGE_PAGE:
                    break
        return {
            "model_error": model_error,
            "counters": {
                "file_facts": facts,
                "file_facts_written": written,
                "passages": passages,
                **{f"drop_{k}": v for k, v in drops.items()},
            },
            "exits": {"extract": "extracted" if model_error is None else "rows_only"},
        }

    async def tracker(state: FileState) -> dict[str, Any]:
        """All 33 rows: proven by a document read, or a stated gap with its reason."""
        from account_intelligence.connectors.ledger import record_document_read
        from account_intelligence.tracker.model import Actor, ReasonCode

        tenant, run, _ = ids(state)
        owners = deps.spec.coverage_owners
        async with deps.txn(tenant) as s:
            facts = (
                await s.execute(
                    text(
                        "SELECT attribute_key FROM facts WHERE run_id = :r AND status = 'current'"
                        " AND attribute_key LIKE 'file.%'"
                    ),
                    {"r": run},
                )
            ).all()
            docs = [str(d.id) for d in await repo.run_documents(s, run)]
        per_category = Counter(f.attribute_key.split(".")[2] for f in facts)
        model_error = state.get("model_error")
        enriched = bool(state.get("enrichment_run_id"))
        done = gaps = 0
        for row in (*cfg.tracker.file_rows, *cfg.tracker.public_only):
            async with deps.txn(tenant) as s:
                service = deps.tracker(s)
                if row in cfg.tracker.file_rows:
                    n = sum(per_category[c] for c in cfg.tracker.file_rows[row])
                    if n:
                        ref = await record_document_read(s, run, row, docs, n)
                        await service.record_outcome(
                            tenant, run, row, [ref], Actor.STAGE, stage=owners.get(row)
                        )
                        done += 1
                        continue
                    reason = (
                        ReasonCode.SOURCE_UNAVAILABLE
                        if model_error
                        else ReasonCode.NOT_IN_UPLOADED_DATA
                    )
                    detail = (
                        "The documents could not be read by the model for this area."
                        if model_error
                        else "The uploaded documents say nothing about this area."
                    )
                else:
                    reason = ReasonCode.NOT_SEARCHED_FILE_ONLY
                    detail = (
                        "Public sources are researched by the linked public research run."
                        if enriched
                        else "This area needs public sources; this run read uploaded files only."
                    )
                await service.record_outcome(
                    tenant, run, row, [], Actor.STAGE, reason, stage=owners.get(row), detail=detail
                )
                gaps += 1
        return {
            "counters": {"rows_from_files": done, "rows_gap": gaps},
            "exits": {"tracker": "all_rows"},
        }

    async def internal_file(state: FileState) -> dict[str, Any]:
        """The Internal Only Word file, stored apart from client files."""
        tenant, run, account = ids(state)
        key, label = await internal_document(deps, documents, tenant, run, account, state)
        return {"exits": {"internal_file": "stored"}, "counters": {"internal_files": 1}}

    async def gate(state: FileState) -> dict[str, Any]:
        """The pre-delivery gate; a failure ends the run partial with its defects."""
        tenant, run, _ = ids(state)
        if deps.gate is None:
            return {"outcome": "partial", "error_code": "gate_not_configured"}
        failing = await deps.gate.check(str(tenant), str(run))
        if any(failing.values()):
            return {"outcome": "partial", "error_code": "gate_failed"}
        return {"exits": {"gate": "passed"}}

    async def deliver(state: FileState) -> dict[str, Any]:
        """Nothing is sent; the outcome is completed unless the model could not read."""
        # nothing is sent and no client file exists in file-only mode: the Internal Only file is
        # stored; a model failure keeps the run partial with its reason
        if state.get("model_error"):
            return {"outcome": "partial", "error_code": state["model_error"]}
        return {"outcome": "completed"}

    async def finish(state: FileState) -> dict[str, Any]:
        """The single settlement point (the runner settles at the terminal state)."""
        if state.get("settled"):
            return {}
        outcome = state.get("outcome", "running")
        return {"settled": True, "outcome": "completed" if outcome == "running" else outcome}

    def ok_or_finish(target: str) -> Any:
        return lambda s: "finish" if s.get("outcome") in ("failed", "partial") else target

    g: StateGraph[FileState] = StateGraph(FileState)
    for name, fn in (
        ("setup", setup),
        ("extract", extract),
        ("tracker", tracker),
        ("internal_file", internal_file),
        ("gate", gate),
        ("deliver", deliver),
        ("finish", finish),
    ):
        g.add_node(name, cast(Any, fn))
    g.add_edge(START, "setup")
    g.add_conditional_edges("setup", ok_or_finish("extract"))
    g.add_edge("extract", "tracker")
    g.add_edge("tracker", "internal_file")
    g.add_edge("internal_file", "gate")
    g.add_conditional_edges("gate", ok_or_finish("deliver"))
    g.add_edge("deliver", "finish")
    g.add_edge("finish", END)
    return g


def _where(location: Mapping[str, Any]) -> str:
    from documents.chunking import cite

    return str(location.get("cite") or cite(dict(location)))


async def findings(s: Any, run: UUID) -> list[Any]:
    """The run's file facts with their citation (document name and location)."""
    from documents.internal_doc import FileFinding

    rows = (
        await s.execute(
            text(
                "SELECT f.id, f.attribute_key, f.value_text, f.product_grade,"
                " e.supporting_sentence,"
                " e.document_location, coalesce(d.name, 'deleted document') AS document_name"
                " FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
                " LEFT JOIN documents d ON d.id = e.document_id"
                " WHERE f.run_id = :r AND f.status = 'current' AND e.evidence_kind = 'document'"
                " ORDER BY f.attribute_key, f.id"
            ),
            {"r": run},
        )
    ).all()
    return [
        FileFinding(
            category=r.attribute_key.split(".")[2],
            value=r.value_text or "",
            grade=r.product_grade,
            citation=f"{r.document_name}, {_where(r.document_location or {})}",
            quote=r.supporting_sentence,
        )
        for r in rows
    ]


async def tracker_lines(s: Any, run: UUID) -> list[Any]:
    """The run's 33 rows with their status and reason text."""
    from account_intelligence.tracker.config import load_checklist, load_tracker_config
    from documents.internal_doc import TrackerLine

    labels = {r.key: r.label for r in load_checklist().rows}
    reasons = load_tracker_config().reason_codes
    rows = (
        await s.execute(
            text(
                "SELECT row_key, status, reason_code FROM coverage_rows WHERE run_id = :r"
                " ORDER BY row_key"
            ),
            {"r": run},
        )
    ).all()
    return [
        TrackerLine(
            labels.get(r.row_key, r.row_key),
            (r.status or "unset").replace("_", " ").capitalize(),
            reasons.get(r.reason_code or "", "") if r.reason_code else "",
        )
        for r in rows
    ]


async def internal_document(
    deps: Any, documents: Any, tenant: UUID, run: UUID, account: UUID, state: Mapping[str, Any]
) -> tuple[str, str]:
    """Build and store the run's Internal Only file once (see ``documents.internal_doc``)."""
    import hashlib

    from documents.gate import INTERNAL_LABEL
    from documents.internal_doc import build

    async with deps.txn(tenant) as s:
        existing = (
            await s.execute(
                text("SELECT object_key, label FROM internal_deliverables WHERE run_id = :r"),
                {"r": run},
            )
        ).first()
        if existing is not None:
            return str(existing.object_key), str(existing.label)
        docs = await repo.run_documents(s, run)
        found = await findings(s, run)
        lines = await tracker_lines(s, run)
    data = build(
        account=str(state.get("account_name") or state.get("domain")),
        as_of=date.fromisoformat(deps.clock.now().date().isoformat()),
        documents=[(d.name, int(d.passages)) for d in docs],
        findings=found,
        tracker=lines,
    )
    sha = hashlib.sha256(data).hexdigest()
    key = f"tenants/{tenant}/internal/{run}/{sha}.docx"

    async def one() -> Any:
        """The file bytes as one chunk."""
        yield data

    await documents.blobs.write_stream(key, one(), max_bytes=len(data))
    async with deps.txn(tenant) as s:
        await repo.store_internal(s, run, account, INTERNAL_LABEL, key, sha, len(data))
    return key, INTERNAL_LABEL


@dataclass(frozen=True)
class FileGraphFactory:
    """The runner's graph for a file-mode run (``workflow.runner.GraphFactory``)."""

    documents: Any

    async def build(self, deps: Any, saver: Any) -> Any:
        """The compiled graph on the run's checkpointer."""
        return build_file_graph(deps, self.documents).compile(checkpointer=saver)

    async def initial(self, deps: Any) -> dict[str, Any]:
        """The first state, from the run row (tenant and run from the trusted run path)."""
        async with deps.txn(deps.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, r.run_input, a.domain, a.name FROM account_runs r"
                        " JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                    ),
                    {"r": deps.run_id},
                )
            ).first()
        if row is None:
            raise LookupError("run not found for this tenant")
        run_input = dict(row.run_input or {})
        state: FileState = {
            "tenant_id": str(deps.tenant_id),
            "run_id": str(deps.run_id),
            "account_id": str(row.account_id),
            "account_name": row.name or row.domain,
            "domain": row.domain,
            "enrichment_run_id": run_input.get("enrichment_run_id"),
            "counters": {},
            "exits": {},
        }
        return dict(state)

    def target(self, values: Mapping[str, Any]) -> tuple[RunStatus, str | None, bool]:
        """``(terminal status, reason, refund_all)`` of a finished file-mode graph."""
        outcome, code = values.get("outcome"), values.get("error_code")
        if outcome == "completed":
            return RunStatus.COMPLETED, None, False
        if outcome == "failed" and code in SETUP_FAILURES:
            return RunStatus.FAILED, str(code), True
        return RunStatus.PARTIAL, str(code or "file_partial"), False


async def is_file_run(deps: Any) -> bool:
    """Whether the run was created in input mode "file"."""
    async with deps.txn(deps.tenant_id) as s:
        mode = (
            await s.execute(
                text("SELECT input_mode FROM account_runs WHERE id = :r"), {"r": deps.run_id}
            )
        ).scalar_one_or_none()
    return bool(mode == "file")
