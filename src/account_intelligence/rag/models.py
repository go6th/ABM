"""Domain models for the agentic RAG subsystem.

Everything here is a typed value object. ``RetrievedChunk`` carries provenance
(source id, retrieval method, span) so every generated claim traces back to the
document it came from — strict chunk attribution, the LLM09 mitigation. ``QaAnswer``
is the generator's structured output: the schema the prompt mandates and the
conditional edges parse. Chunk ACLs travel with the chunk so retrieval can
enforce them before any text reaches the context window (LLM02).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RetrievalMethod = Literal["dense", "sparse", "graph", "rerank"]

INSUFFICIENT_CONTEXT = (
    "INSUFFICIENT_CONTEXT: The required information is not available in the current knowledge base."
)


class ChunkACL(BaseModel):
    """Access control list for one chunk: tenant, roles and clearance floor."""

    tenant_id: str
    allowed_roles: frozenset[str] = Field(default_factory=frozenset)
    min_clearance: int = 0


class RetrievedChunk(BaseModel):
    """One retrieved chunk with its provenance and retrieval-time score."""

    chunk_id: str
    source_id: str
    text: str
    score: float = Field(ge=0.0, le=1.0)
    method: RetrievalMethod = "dense"
    metadata: dict[str, str] = Field(default_factory=dict)
    acl: ChunkACL | None = None


class Citation(BaseModel):
    """An inline citation: one claim mapped to the chunk that supports it."""

    chunk_id: str
    source_id: str
    span: str | None = None


class ReflectionVerdict(BaseModel):
    """The reflection node's critique of the retrieved context."""

    sufficient: bool
    relevance: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    quality: float = Field(ge=0.0, le=1.0)
    gaps: tuple[str, ...] = ()
    refined_query: str | None = None


class QaAnswer(BaseModel):
    """The generator's structured output (the prompt's JSON schema)."""

    reasoning_trace: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    requires_more_info: bool
    final_answer: str
    citations: tuple[Citation, ...] = ()

    @property
    def is_refusal(self) -> bool:
        """True when the answer is the protocol's refusal payload."""
        return self.requires_more_info and (
            not self.final_answer.strip() or self.final_answer == INSUFFICIENT_CONTEXT
        )


class RetrievalResult(BaseModel):
    """The retrieval layer's output: ranked chunks plus how they were found."""

    chunks: tuple[RetrievedChunk, ...] = ()
    methods_used: tuple[str, ...] = ()
    candidate_count: int = 0
    fused_scores: dict[str, float] = Field(default_factory=dict)
