from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from account_intelligence.workflow.coverage import CoverageLedger, CoverageProof
from account_intelligence.workflow.engine import build_graph
from account_intelligence.workflow.evidence_gate import EvidenceGate, EvidenceItem, ProposedFact
from account_intelligence.workflow.ports import NotConfigured
from account_intelligence.workflow.spec import (
    StageSpec,
    _coverage_owners,
    _topological,
    load_spec,
)
from account_intelligence.workflow.stage import Stage
from account_intelligence.workflow.state import (
    append_unique,
    merge_maps,
    migrate,
    sum_counters,
    validate_small_state,
)
from account_intelligence.workflow.transitions import RunStatus, transition
from tests.workflow.fakes import FakeLlm


def test_spec_has_valid_dag_and_total_coverage_ownership() -> None:
    spec = load_spec()
    assert len(spec.coverage_owners) == 33
    assert set(spec.coverage_owners) == set(spec.coverage_rows)
    assert spec.topological_order[-1] == "deliver"
    owners = {stage.id: stage.depends_on for stage in spec.stages}
    assert "people" in owners["name_searches"]
    assert "verification" in owners["distill_grade"]
    assert "distill_grade" in owners["build"]
    assert "build" in owners["gate"]
    assert "gate" in owners["deliver"]


def test_spec_rejects_invalid_stage_dependencies_and_budget_fields() -> None:
    stage_x = StageSpec("x", "X", (), (), 0, 0, ())
    with pytest.raises(ValueError, match="unique"):
        _topological((stage_x, stage_x))
    invalid = (
        (StageSpec("x", "X", ("missing",), (), 0, 0, ()), "unknown stages"),
        (StageSpec("x", "X", ("x",), (), 0, 0, ()), "itself"),
        (StageSpec("x", "X", (), (), 1.1, 0, ()), "budget_share"),
        (StageSpec("x", "X", (), (), 0, -1, ()), "loop_cap"),
    )
    for stage, message in invalid:
        with pytest.raises(ValueError, match=message):
            _topological((stage,))
    cycle = (
        StageSpec("x", "X", ("y",), (), 0, 0, ()),
        StageSpec("y", "Y", ("x",), (), 0, 0, ()),
    )
    with pytest.raises(ValueError, match="cycle"):
        _topological(cycle)
    duplicate_coverage = (
        *load_spec().stages,
        StageSpec("extra", "Extra", (), (), 0, 0, ("company_news",)),
    )
    with pytest.raises(ValueError, match="multiple owners"):
        _coverage_owners(duplicate_coverage, load_spec().coverage_rows)
    with pytest.raises(ValueError, match="unknown"):
        _coverage_owners((StageSpec("extra", "Extra", (), (), 0, 0, ("bad",)),), frozenset())
    with pytest.raises(ValueError, match="missing"):
        _coverage_owners((), frozenset({"prior_customer_status"}))


@pytest.mark.asyncio
async def test_spec_driven_graph_executes_all_stages_in_order() -> None:
    spec = load_spec()
    seen: list[str] = []

    def make_handler(stage_id: str, rows: tuple[str, ...]):
        async def handle(_state):  # type: ignore[no-untyped-def]
            seen.append(stage_id)
            return {
                "coverage_updates": {
                    row: {"status": "none", "proof": {"reason": "synthetic no-result fixture"}}
                    for row in rows
                },
                "references": {f"{stage_id}_result": f"synthetic:{stage_id}"},
            }

        return handle

    handlers = {stage.id: make_handler(stage.id, stage.coverage) for stage in spec.stages}
    graph = build_graph(handlers, spec).compile()
    final = await graph.ainvoke(
        {"tenant_id": "tenant-test", "run_id": "run-test", "completed_stages": []}
    )
    assert tuple(seen) == spec.topological_order
    assert set(final["completed_stages"]) == set(spec.topological_order)
    assert len(final["references"]) == len(spec.stages)


def test_coverage_requires_proof_and_terminal_high_rows() -> None:
    ledger = CoverageLedger.from_config()
    with pytest.raises(ValueError, match="proof"):
        ledger.set_status("company_news", "done", CoverageProof())
    ledger.set_status(
        "company_news",
        "blocked",
        CoverageProof(
            queries=("company newsroom",),
            tool_category="web_search",
            result_count=0,
            reason="source unavailable",
        ),
    )
    assert ledger.high_rows_resolved() is False
    with pytest.raises(ValueError, match="tool category"):
        CoverageProof(queries=("query",)).validate()
    with pytest.raises(ValueError, match="negative"):
        CoverageProof(reason="no results", result_count=-1).validate()
    with pytest.raises(ValueError, match="result count"):
        CoverageProof(tool_category="web_search").validate()
    with pytest.raises(KeyError, match="unknown"):
        ledger.set_status("bad-row", "none", CoverageProof(reason="n/a"))
    with pytest.raises(ValueError, match="reason"):
        ledger.set_status("prior_customer_status", "blocked", CoverageProof(source_refs=("S-001",)))
    with pytest.raises(ValueError, match="blank"):
        CoverageProof(reason="  ").validate()
    with pytest.raises(ValueError, match="blocked"):
        ledger.set_status("prior_customer_status", "blocked", CoverageProof(source_refs=("S-001",)))
    complete = CoverageLedger({"prior_customer_status": "high"})
    complete.set_status("prior_customer_status", "none", CoverageProof(reason="not applicable"))
    assert complete.high_rows_resolved()


def test_evidence_gate_drops_missing_or_unverifiable_evidence() -> None:
    gate = EvidenceGate()
    retrieved = datetime(2026, 10, 1, tzinfo=UTC)
    sources = {"S-0001": "https://example.com/release"}
    raw = {"S-0001": "Revenue was $10 million in 2025."}
    ok = ProposedFact(
        claim="Revenue was $10 million in 2025",
        evidence=(EvidenceItem("S-0001", "Revenue was $10 million in 2025", retrieved),),
        product_grade="confirmed",
        read_in_primary_this_run=True,
    )
    bad = ProposedFact(
        claim="Revenue was $11 million in 2025",
        evidence=(EvidenceItem("S-0001", "Revenue was $11 million in 2025", retrieved),),
    )
    assert gate.validate(ok, raw, sources).accepted
    result = gate.validate(bad, raw, sources)
    assert not result.accepted
    assert result.drop_reason == "unverifiable_quote"
    wrong_url = ProposedFact(
        "Revenue was $10 million in 2025",
        (
            EvidenceItem(
                "S-0001", "Revenue was $10 million in 2025", retrieved, "https://example.com/other"
            ),
        ),
    )
    assert gate.validate(wrong_url, raw, sources).drop_reason == "untrusted_url"


def test_hypothesis_requires_question_and_confirmed_requires_primary_fetch() -> None:
    gate = EvidenceGate()
    retrieved = datetime(2026, 10, 1, tzinfo=UTC)
    source_url = "https://example.com/news"
    raw = {"S-0001": "The company hired a chief data officer."}
    sources = {"S-0001": source_url}
    no_question = ProposedFact(
        "The company may be investing in data",
        (EvidenceItem("S-0001", "hired a chief data officer", retrieved),),
        product_grade="hypothesis",
    )
    assert gate.validate(no_question, raw, sources).drop_reason == "hypothesis_missing_question"
    non_primary = ProposedFact(
        "The company hired a chief data officer",
        (EvidenceItem("S-0001", "hired a chief data officer", retrieved),),
        product_grade="confirmed",
        read_in_primary_this_run=False,
    )
    assert gate.validate(non_primary, raw, sources).drop_reason == "confirmed_without_primary_read"
    assert gate.validate(ProposedFact("empty", ()), raw, sources).drop_reason == "missing_evidence"
    unknown = ProposedFact("claim", (EvidenceItem("S-9999", "some quote", retrieved),))
    assert gate.validate(unknown, raw, sources).drop_reason == "unknown_source"
    false_figure = ProposedFact(
        "Revenue was $12 million",
        (EvidenceItem("S-0001", "hired a chief data officer", retrieved),),
        figure="$12 million",
    )
    assert gate.validate(false_figure, raw, sources).drop_reason == "unverifiable_figure"


def test_transition_table_is_optimistic_and_terminal() -> None:
    assert transition(RunStatus.QUEUED, RunStatus.RUNNING) is RunStatus.RUNNING
    with pytest.raises(ValueError, match="illegal"):
        transition(RunStatus.COMPLETED, RunStatus.RUNNING)
    assert (
        transition(RunStatus.QUEUED, RunStatus.RUNNING, actual=RunStatus.CANCELED)
        is RunStatus.CANCELED
    )
    with pytest.raises(ValueError, match="illegal"):
        transition(RunStatus.RUNNING, RunStatus.BUDGET_EXCEEDED)


def test_state_migration_drops_old_graph_shape_and_rejects_embedded_facts() -> None:
    migrated = migrate({"schema_version": 0, "tenant_id": "t", "run_id": "r", "spent_usd": 1.0})
    assert migrated["schema_version"] == 1
    assert migrated["spent_usd"] == 1.0
    with pytest.raises(ValueError, match="payloads"):
        migrate({"schema_version": 0, "facts": [{"claim": "text"}]})
    with pytest.raises(ValueError, match="newer"):
        migrate({"schema_version": 99})
    with pytest.raises(ValueError, match="64 KiB"):
        validate_small_state({"references": {"x": "a" * (65 * 1024)}})
    with pytest.raises(ValueError, match="4 KiB"):
        validate_small_state({"references": {"x": "a" * 5000}})
    with pytest.raises(ValueError, match="text or payloads"):
        validate_small_state({"references": {"raw_text": "private text"}})
    with pytest.raises(ValueError, match="text or payloads"):
        validate_small_state({"references": [{"nested": {"prompt": "private"}}]})
    assert append_unique(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert sum_counters({"calls": 1}, {"calls": 2}) == {"calls": 3}
    with pytest.raises(ValueError, match="conflict"):
        merge_maps({"reference": "one"}, {"reference": "two"})


@pytest.mark.asyncio
async def test_stage_replay_is_noop_and_owned_coverage_must_be_complete() -> None:
    spec = next(item for item in load_spec().stages if item.id == "news")
    calls = 0

    async def handler(_state):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {
            "coverage_updates": {
                row: {"status": "none", "proof": {"reason": "not found"}} for row in spec.coverage
            }
        }

    stage = Stage(spec, handler)
    state = {"tenant_id": "trusted", "run_id": "run-1", "completed_stages": []}
    first = await stage.run(state)
    await stage.run({**state, "completed_stages": first["completed_stages"]})
    assert calls == 1

    async def incomplete(_state):  # type: ignore[no-untyped-def]
        return {"coverage_updates": {}}

    with pytest.raises(ValueError, match="each owned"):
        await Stage(spec, incomplete).run(state)
    with pytest.raises(ValueError, match="tenant_id and run_id"):
        await stage.run({"run_id": "run-1"})
    with pytest.raises(NotConfigured):
        await Stage(spec).run(state)

    async def protected(_state):  # type: ignore[no-untyped-def]
        return {"tenant_id": "forged", "coverage_updates": {}}

    with pytest.raises(ValueError, match="protected"):
        await Stage(spec, protected).run(state)

    async def bad_proof(_state):  # type: ignore[no-untyped-def]
        return {"coverage_updates": {row: {"status": "done", "proof": {}} for row in spec.coverage}}

    with pytest.raises(ValueError, match="lacks status or proof"):
        await Stage(spec, bad_proof).run(state)


def test_engine_rejects_parallel_mode_until_enabled_by_design() -> None:
    spec = load_spec()
    unsupported = replace(spec, settings={**spec.settings, "execution_mode": "parallel"})
    with pytest.raises(ValueError, match="sequential"):
        build_graph({}, unsupported)


@pytest.mark.asyncio
async def test_fake_llm_is_scripted_by_template_and_canonical_input_and_validates_schema() -> None:
    class Output(BaseModel):
        answer: str

    request = {"b": 2, "a": 1}
    key = ("extract.v1", FakeLlm.input_hash(request))
    valid = FakeLlm({key: {"answer": "synthetic"}})
    result = await valid.generate("extractor", key[0], request, Output)
    assert result.answer == "synthetic"
    with pytest.raises(ValidationError):
        await FakeLlm({key: {"unexpected": "fabricated"}}).generate(
            "extractor", key[0], request, Output
        )
    with pytest.raises(AssertionError, match="unrecorded"):
        await valid.generate("extractor", key[0], {"other": True}, Output)
