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
    assert state["error_code"] == "build_failed:build_failed"
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
        fact_ids = [f["fact_id"] for f in payload["facts"][:1]]
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
