"""Failures that used to end a run FAILED after its pulls were paid for (2026-10-12) now end it
partial with the reason, or are dropped and counted: a refused build, a model provider out of
credits, an extraction group too large for the model, and a narrative the guards dropped whole."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from exports.builder import BuildFailed
from exports.models import BuildResult
from llm.errors import ContextTooLong, ProviderCreditsExhausted
from tests.agents.conftest import Harness

pytestmark = pytest.mark.asyncio


def script(h: Harness, task: str, fn: Any) -> None:
    assert h.llm is not None
    h.llm.scripts[task] = fn


async def test_a_refused_build_ends_the_run_partial_with_the_builders_reason(h: Harness) -> None:
    async def refusing(ctx, audience):  # type: ignore[no-untyped-def]
        raise BuildFailed(
            BuildResult(status="failed", reason="build_failed", errors=("missing_narrative",))
        )

    h.builder.build = refusing  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["outcome"] == "partial"
    assert state["error_code"] == "build_failed:missing_narrative"  # says why
    assert state["settled"] is True  # the run reached its single settlement, no exception


async def test_a_model_out_of_credits_ends_the_run_partial_and_keeps_the_pulls(
    h: Harness,
) -> None:
    def broke(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ProviderCreditsExhausted("credit balance too low")

    script(h, "abm_distill", broke)
    record, state = await h.run("r")
    assert state["outcome"] == "failed" and state["error_code"] == "model_credits_exhausted"
    assert state["settled"] is True
    assert h.manifests.by_run[record.run_id].rows  # the pulled manifest is kept for a rebuild
    assert state["counters"]["rows_stored"] > 0


async def test_an_extraction_group_too_large_is_dropped_not_fatal(h: Harness) -> None:
    def too_long(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ContextTooLong("prompt is too long")

    script(h, "abm_pass1_extract", too_long)
    _, state = await h.run("r")
    assert state["counters"]["drop_context_too_long"] >= 1
    assert state["outcome"] in ("completed", "partial")
    assert state.get("error_code") in (None, "gate_failed")


async def test_a_narrative_dropped_whole_is_asked_for_once_more(h: Harness) -> None:
    calls: list[Mapping[str, Any]] = []

    def grade_first_then_clean(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append(payload)
        # a statement may rest only on a fact the policy lets a client file state
        fact_ids = list(payload["statable_fact_ids"][:1]) or [payload["facts"][0]["fact_id"]]
        text = (
            "Recent activity points to a platform investment."
            if payload.get("notes")
            else "Probable: the account is investing in a platform."
        )
        return {
            "hypotheses": [],
            "questions": [],
            "narrative": [{"text": text, "fact_ids": fact_ids}],
        }

    script(h, "abm_distill", grade_first_then_clean)
    record, state = await h.run("r")
    counters = state["counters"]
    assert counters["distill_client_drop_restates_grade"] == 1
    assert counters["distill_client_drop_narrative_reasked"] == 1
    assert "rejected" in str(calls[1].get("notes"))
    client = h.records.distilled[(record.run_id, "client")]
    assert [c.text for c in client.narrative] == [
        "Recent activity points to a platform investment."
    ]


async def test_a_model_failure_part_way_keeps_earlier_findings_and_progress(h: Harness) -> None:
    """The model refuses at the second extraction step (credits, usage limit): what the first
    step found is graded and kept, and every pulled row is reported to the tracker, so a stopped
    run shows its findings and progress instead of nothing (audit 2026-10-15)."""

    def broke(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ProviderCreditsExhausted("usage limit reached")

    script(h, "abm_pass2_extract", broke)
    record, state = await h.run("r")
    assert state["outcome"] == "failed" and state["error_code"] == "model_credits_exhausted"
    assert h.grading.facts_by_run[record.run_id]  # pass1 findings were graded and kept
    assert h.tracker.rows_by_run[record.run_id]  # pulled rows reported before extraction


async def test_a_narrative_that_states_an_unconfirmed_fact_becomes_a_question(h: Harness) -> None:
    """Only a Confirmed fact may be stated to a prospect. A narrative sentence that states any
    other fact is dropped here (not withheld silently later), the model is asked once more, and
    a narrative still empty after that is made of the first-call questions, so the Summary
    always has an honest opening (audit 2026-10-15: every such run failed its build)."""
    calls: list[Mapping[str, Any]] = []

    def states_probable(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append(payload)
        statable = set(payload["statable_fact_ids"])
        other = next(f["fact_id"] for f in payload["facts"] if f["fact_id"] not in statable)
        return {
            "hypotheses": [],
            "questions": [
                {"text": "Is the platform work the current priority?", "fact_ids": [other]}
            ],
            "narrative": [{"text": "The account is investing in a platform.", "fact_ids": [other]}],
        }

    script(h, "abm_distill", states_probable)
    record, state = await h.run("r")
    counters = state["counters"]
    assert counters["distill_client_drop_unconfirmed_stated"] == 2  # both attempts stated it
    assert counters["distill_client_drop_narrative_reasked"] == 1
    assert "statable_fact_ids" in str(calls[1].get("notes"))
    assert counters["distill_client_drop_narrative_from_questions"] == 1
    client = h.records.distilled[(record.run_id, "client")]
    assert [c.text for c in client.narrative] == ["Is the platform work the current priority?"]
    assert all(f in client.narrative[0].fact_ids for f in client.questions[0].fact_ids)
    assert "internal" not in {a for (_, a) in h.records.distilled}  # one distill call per run


async def test_a_run_that_graded_no_fact_finishes_with_no_new_findings(h: Harness) -> None:
    """Audit 2026-10-20: refreshes whose every candidate was dropped ended "build failed
    (missing content)" and offered a Resume that re-paid the same empty extraction."""
    built: list[str] = []

    async def nothing(ctx):  # type: ignore[no-untyped-def]
        return ()

    async def build(ctx, audience):  # type: ignore[no-untyped-def]
        built.append(audience)
        return ()

    h.grading.facts = nothing  # type: ignore[method-assign]
    h.builder.build = build  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["outcome"] == "completed" and state["error_code"] == "no_new_findings"
    assert built == []  # nothing to build, so the builder is never asked
    assert state["settled"] is True


def test_no_new_findings_is_a_finished_run_not_a_stop() -> None:
    from account_intelligence.workflow.transitions import RunStatus
    from agents.production.wiring import AbmGraphFactory

    status, reason, refund = AbmGraphFactory.target(
        None,  # type: ignore[arg-type]
        {"outcome": "completed", "error_code": "no_new_findings", "mode": "full"},
    )
    assert (status, reason, refund) == (RunStatus.COMPLETED, "no_new_findings", False)
