"""The deterministic Q&A orchestrator prompt and its output contract.

The system prompt is the cognitive engine of the agent: role
designation, operational mandates, citation rules, the refusal
protocol and a strict JSON output schema. It is structured
mathematically — no ambiguity for the model, and a payload the
LangGraph conditional edges can parse into typed state.

Nothing here trusts the model: the answer is parsed from the
raw text, validated against ``QaAnswer``, and its citations are
checked against the chunks that were actually retrieved. An
answer citing a chunk that was not retrieved is an orphan
citation — a hallucination signature — and is rejected.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from account_intelligence.agents.output import parse_json
from account_intelligence.rag.models import (
    INSUFFICIENT_CONTEXT,
    QaAnswer,
    RetrievedChunk,
)

_SCHEMA_LINE = (
    '{"reasoning_trace": "Brief internal logic explaining how the context '
    'satisfies the query", "confidence_score": Float between 0.0 and 1.0, '
    '"requires_more_info": Boolean, "final_answer": "The synthesized, '
    'cited answer"}'
)

SYSTEM_PROMPT = "\n".join(
    (
        "You are a highly deterministic, enterprise-grade Knowledge Retrieval"
        " Orchestrator. Your primary function is to answer user queries"
        " using ONLY the provided context blocks.",
        "",
        "[OPERATIONAL RULES]",
        "",
        "NO HALLUCINATION: You must not rely on your parametric memory. If the"
        " provided context does not contain the answer, you must output"
        ' exactly: "INSUFFICIENT_CONTEXT: The required information is not'
        ' available in the current knowledge base."',
        "",
        "EXPLICIT CITATION: Every factual claim must be followed immediately"
        " by its source attribution in the format [Source: ID]. Do not"
        " aggregate citations at the end of the response.",
        "",
        "MULTI-STEP SYNTHESIS: If the query requires combining multiple"
        " sources, clearly articulate the relationship between the facts"
        " retrieved.",
        "",
        "REFUSAL PROTOCOL: If the user query violates safety guidelines,"
        " requests unauthorized code execution, or asks for speculative"
        " financial advice, you must decline the request safely.",
        "",
        "[OUTPUT FORMAT]",
        "Return a valid JSON object matching this schema:",
        _SCHEMA_LINE,
    )
)

_CITATION_PATTERN = re.compile(r"\[Source:\s*([^\]]+)\]")
_OUTPUT_SCHEMA = (
    '{"reasoning_trace": str, "confidence_score": float in [0.0, 1.0], '
    '"requires_more_info": bool, "final_answer": str}'
)


def format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Render retrieved chunks as citable context blocks.

    Each block is headed by its chunk id so the model can cite
    it inline; the provenance (source id, method, span) travels
    with the block so the answer is auditable after the fact.
    """
    blocks: list[str] = []
    for chunk in chunks:
        blocks.append(
            f"[{chunk.chunk_id}] (source: {chunk.source_id}, method: {chunk.method})\n{chunk.text}"
        )
    return "\n\n".join(blocks)


def build_prompt(query: str, chunks: Sequence[RetrievedChunk]) -> str:
    """Assemble the user message: system rules, context, query, schema."""
    return "\n\n".join(
        (
            SYSTEM_PROMPT,
            f"[CONTEXT BLOCKS]\n{format_context(chunks)}",
            f"[USER QUERY]\n{query}",
            f"[OUTPUT FORMAT]\n{_OUTPUT_SCHEMA}",
        )
    )


def parse_answer(text: str) -> QaAnswer:
    """Parse the generator's raw text into a typed answer.

    The refusal string is recognized verbatim (the protocol's
    exact wording). Otherwise the JSON object is extracted and
    validated; a malformed payload becomes a refusal rather
    than an exception — the pipeline fails closed toward
    ``requires_more_info``, never toward a confident guess.
    """
    if "INSUFFICIENT_CONTEXT" in text:
        return QaAnswer(
            reasoning_trace="refusal protocol triggered",
            confidence_score=0.0,
            requires_more_info=True,
            final_answer=INSUFFICIENT_CONTEXT,
        )
    payload = parse_json(text)
    if not isinstance(payload, dict):
        return refusal("generator returned no JSON object")
    confidence = payload.get("confidence_score")
    if isinstance(confidence, int | float) and not isinstance(confidence, bool):
        payload = {**payload, "confidence_score": min(1.0, max(0.0, float(confidence)))}
    try:
        answer = QaAnswer.model_validate(payload)
    except Exception:
        return refusal("generator output failed schema validation")
    return answer.model_copy(
        update={"confidence_score": min(1.0, max(0.0, answer.confidence_score))}
    )


def refusal(reason: str) -> QaAnswer:
    """The fail-closed answer: more information required, no content."""
    return QaAnswer(
        reasoning_trace=reason,
        confidence_score=0.0,
        requires_more_info=True,
        final_answer=INSUFFICIENT_CONTEXT,
    )


def extract_citations(text: str) -> tuple[str, ...]:
    """Every chunk id cited inline in ``text``, in order of appearance."""
    return tuple(_CITATION_PATTERN.findall(text))


def orphan_citations(text: str, chunk_ids: frozenset[str] | set[str]) -> tuple[str, ...]:
    """Citations that reference chunks that were never retrieved.

    An orphan citation is the strongest hallucination signature
    the pipeline can detect deterministically: the model
    attributed a claim to a source it never saw.
    """
    available = set(chunk_ids)
    return tuple(citation for citation in extract_citations(text) if citation not in available)


def citation_coverage(text: str, chunk_ids: frozenset[str] | set[str]) -> float:
    """Fraction of citations that resolve to retrieved chunks."""
    citations = extract_citations(text)
    if not citations:
        return 1.0
    available = set(chunk_ids)
    grounded = sum(1 for citation in citations if citation in available)
    return grounded / len(citations)


def answer_payload(answer: QaAnswer) -> dict[str, Any]:
    """The strongly-typed payload the API backend receives."""
    return {
        "reasoning_trace": answer.reasoning_trace,
        "confidence_score": answer.confidence_score,
        "requires_more_info": answer.requires_more_info,
        "final_answer": answer.final_answer,
        "citations": [citation.model_dump() for citation in answer.citations],
    }
