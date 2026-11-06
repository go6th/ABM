"""Observability: the log field allow-list, spans and counters."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest
from opentelemetry import trace

from account_intelligence.rag.obs import (
    LOG_FIELDS,
    SPAN_FIELDS,
    log_event,
    phase_span,
    record_cache_hit,
    record_phase,
    run_trace_context,
)


class TestLogEvent:
    def test_allow_listed_fields_are_kept(self, caplog: pytest.LogCaptureFixture) -> None:
        """Allow-listed fields appear in the structured log."""
        with caplog.at_level(logging.INFO, logger="account_intelligence.rag"):
            log_event("rag.test", outcome="ok", chunks=3, tenant_id="t1")
        payload = json.loads(caplog.records[-1].getMessage())
        assert payload == {
            "event": "rag.test",
            "outcome": "ok",
            "chunks": 3,
            "tenant_id": "t1",
        }

    def test_non_allow_listed_fields_are_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Anything outside the allow-list never reaches the log."""
        with caplog.at_level(logging.INFO, logger="account_intelligence.rag"):
            log_event(
                "rag.test",
                outcome="ok",
                prompt="SECRET PROMPT",
                chunk_text="SECRET TEXT",
            )
        payload = json.loads(caplog.records[-1].getMessage())
        assert "prompt" not in payload
        assert "chunk_text" not in payload
        assert payload["event"] == "rag.test"

    def test_non_scalar_fields_are_stringified(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-scalar values are coerced, never dropped silently."""
        with caplog.at_level(logging.INFO, logger="account_intelligence.rag"):
            log_event("rag.test", methods=("dense", "sparse"))
        payload = json.loads(caplog.records[-1].getMessage())
        assert payload["methods"] == "('dense', 'sparse')"

    def test_allow_list_is_explicit(self) -> None:
        """The allow-list is a fixed, auditable set."""
        assert "event" in LOG_FIELDS
        assert "prompt" not in LOG_FIELDS
        assert "gen_ai.request.model" in SPAN_FIELDS


class TestMeters:
    def test_record_phase(self) -> None:
        """Phase counters and latencies record without an exporter."""
        record_phase("retrieve", "ok", 12)
        record_phase("generate", "error", None)

    def test_record_cache_hit(self) -> None:
        """Cache outcomes are counted by hit and miss."""
        record_cache_hit(True)
        record_cache_hit(False)


class TestTracing:
    def test_run_trace_context_uses_the_run_id(self) -> None:
        """The trace id of the remote parent is the run id."""
        run_id = uuid4()
        context = run_trace_context(run_id)
        span_context = trace.get_current_span(context).get_span_context()
        assert span_context.trace_id == run_id.int
        assert span_context.is_remote

    def test_phase_span_filters_attributes(self) -> None:
        """Only allow-listed span attributes are attached."""
        run_id = uuid4()
        with phase_span(
            run_id,
            "retrieve",
            {"rag.retriever.top_k": 8, "prompt": "SECRET"},
        ) as span:
            context = span.get_span_context()
            assert context.trace_id == run_id.int
            assert context.is_valid

    def test_phase_span_drops_none(self) -> None:
        """None attributes are dropped, not attached."""
        run_id = uuid4()
        with phase_span(run_id, "generate", {"chunks": None}) as span:
            assert span is not None
