"""Prompt assembly, answer parsing and citation auditing."""

from __future__ import annotations

import json

import pytest

from account_intelligence.rag.models import (
    Citation,
    INSUFFICIENT_CONTEXT,
    RetrievedChunk,
)
from account_intelligence.rag.prompt import (
    SYSTEM_PROMPT,
    answer_payload,
    build_prompt,
    citation_coverage,
    extract_citations,
    format_context,
    orphan_citations,
    parse_answer,
    refusal,
)


def _chunk(chunk_id: str, text: str) -> RetrievedChunk:
    """One citable chunk."""
    return RetrievedChunk(chunk_id=chunk_id, source_id=f"src-{chunk_id}", text=text, score=0.5)


class TestPrompt:
    def test_system_prompt_mentions_the_contract(self) -> None:
        """The system prompt carries the citation and refusal rules."""
        for marker in (
            "NO HALLUCINATION",
            "EXPLICIT CITATION",
            "[Source: ID]",
            "REFUSAL PROTOCOL",
            "INSUFFICIENT_CONTEXT",
        ):
            assert marker in SYSTEM_PROMPT

    def test_format_context_blocks(self) -> None:
        """Each block is headed by its chunk id and provenance."""
        blocks = format_context((_chunk("c1", "acme revenue"),))
        assert blocks.startswith("[c1]")
        assert "source: src-c1" in blocks
        assert "acme revenue" in blocks

    def test_build_prompt_contains_every_section(self) -> None:
        """The user message carries rules, context, query and schema."""
        prompt = build_prompt("what is acme revenue?", (_chunk("c1", "acme"),))
        assert SYSTEM_PROMPT in prompt
        assert "[USER QUERY]" in prompt
        assert "what is acme revenue?" in prompt
        assert "[OUTPUT FORMAT]" in prompt
        assert "reasoning_trace" in prompt


class TestParseAnswer:
    def test_valid_json(self) -> None:
        """A schema-shaped JSON object parses into a typed answer."""
        payload = {
            "reasoning_trace": "cited the context",
            "confidence_score": 0.9,
            "requires_more_info": False,
            "final_answer": "Acme revenue grew [Source: c1]",
        }
        answer = parse_answer(json.dumps(payload))
        assert answer.confidence_score == 0.9
        assert answer.final_answer == "Acme revenue grew [Source: c1]"

    def test_refusal_string(self) -> None:
        """The protocol's exact wording is recognized verbatim."""
        answer = parse_answer(INSUFFICIENT_CONTEXT)
        assert answer.is_refusal
        assert answer.confidence_score == 0.0

    def test_malformed_json_refuses(self) -> None:
        """A non-JSON answer fails closed, never to a guess."""
        answer = parse_answer("I am not a JSON object")
        assert answer.is_refusal
        assert "no JSON object" in answer.reasoning_trace

    def test_schema_violation_refuses(self) -> None:
        """A JSON object that misses required keys refuses."""
        answer = parse_answer(json.dumps({"reasoning_trace": "incomplete"}))
        assert answer.is_refusal
        assert "schema validation" in answer.reasoning_trace

    def test_confidence_is_clamped(self) -> None:
        """Out-of-range confidence is clamped into [0, 1]."""
        payload = {
            "reasoning_trace": "cited",
            "confidence_score": 42.0,
            "requires_more_info": False,
            "final_answer": "answer",
        }
        assert parse_answer(json.dumps(payload)).confidence_score == 1.0

    def test_json_embedded_in_prose(self) -> None:
        """The object is found between the first brace and the last."""
        payload = (
            '{"reasoning_trace": "cited", "confidence_score": 0.5,'
            ' "requires_more_info": false, "final_answer": "ok"}'
        )
        answer = parse_answer(f"Sure! Here it is: {payload} hope that helps")
        assert not answer.is_refusal
        assert answer.final_answer == "ok"


class TestCitations:
    def test_extract_citations_in_order(self) -> None:
        """Inline citations are extracted in order of appearance."""
        text = "A grew [Source: c1] while B fell [Source: c2] and C [Source: c1]"
        assert extract_citations(text) == ("c1", "c2", "c1")

    def test_orphan_citations(self) -> None:
        """A citation to an unretrieved chunk is a hallucination signature."""
        text = "A grew [Source: c1] and B [Source: ghost]"
        assert orphan_citations(text, {"c1"}) == ("ghost",)

    def test_no_orphans_when_all_resolve(self) -> None:
        """Every citation resolving means no orphans."""
        assert orphan_citations("[Source: c1]", {"c1"}) == ()

    def test_citation_coverage(self) -> None:
        """Coverage is the fraction of citations that resolve."""
        text = "A [Source: c1] B [Source: c2] C [Source: ghost]"
        assert citation_coverage(text, {"c1", "c2"}) == pytest.approx(2 / 3)

    def test_citation_coverage_without_citations(self) -> None:
        """An uncited answer has perfect coverage by definition."""
        assert citation_coverage("no citations here", {"c1"}) == 1.0


class TestRefusal:
    def test_refusal_payload(self) -> None:
        """The refusal carries the reason and zero confidence."""
        answer = refusal("the context is thin")
        assert answer.is_refusal
        assert answer.reasoning_trace == "the context is thin"
        assert answer.confidence_score == 0.0
        assert answer.final_answer == INSUFFICIENT_CONTEXT

    def test_answer_payload_shape(self) -> None:
        """The API payload is strongly typed with citations."""
        answer = refusal("thin").model_copy(
            update={"citations": (Citation(chunk_id="c1", source_id="src-c1"),)}
        )
        payload = answer_payload(answer)
        assert payload["citations"] == [{"chunk_id": "c1", "source_id": "src-c1", "span": None}]
        assert payload["requires_more_info"] is True
