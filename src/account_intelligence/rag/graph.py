"""The agentic Q&A loop: a LangGraph state machine over the pure modules.

The graph is the blueprint's orchestrator: ``guard -> route ->
retrieve -> reflect -> generate``, with conditional edges that loop a
refined query back into retrieval, a circuit breaker on total retrieval
passes (OWASP LLM10), a human-review interrupt for low-confidence
answers, and a fail-closed refusal node. A hallucinated citation — an
answer that references a chunk that was never retrieved — is routed to
the refusal node rather than delivered.

State discipline follows the platform rule: the checkpointed state
carries ids, counters and the answer payload only. Chunk text, the
prompt and the raw model output never enter the state — retrieval
results live in the loop's in-run store for the duration of the query
(the way the workflow keeps payloads in their store), and
``validate_state`` refuses any state that grows text fields or exceeds
the 64 KiB checkpoint budget.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from account_intelligence.platform.ports import Clock, LlmPort, NotConfigured
from account_intelligence.rag.access import Subject, filter_by_acl
from account_intelligence.rag.cache import EmbedderPort, SemanticCache
from account_intelligence.rag.config import RagConfig
from account_intelligence.rag.graphrag import (
    GraphRetrieverPort,
    KnowledgeGraph,
    extract_seed_entities,
    route_query,
)
from account_intelligence.rag.guardrails import GuardrailPort
from account_intelligence.rag.models import (
    Citation,
    RetrievedChunk,
)
from account_intelligence.rag.obs import log_event, phase_span
from account_intelligence.rag.prompt import (
    SYSTEM_PROMPT,
    answer_payload,
    build_prompt,
    extract_citations,
    orphan_citations,
    parse_answer,
    refusal,
)
from account_intelligence.rag.reflection import (
    JudgePort,
    decide,
    evaluate,
)
from account_intelligence.rag.retrieval import (
    DenseRetrieverPort,
    RerankerPort,
    SparseRetrieverPort,
    drop_below,
    metadata_filter,
    rerank,
    select_top_k,
    weighted_reciprocal_rank_fusion,
)

GENERATOR_TIER = "generator"
GENERATE_MAX_TOKENS = 512
GRAPH_HOPS = 2
MAX_STATE_BYTES = 64 * 1024
FORBIDDEN_STATE_KEYS = frozenset({"prompt", "context", "chunks", "raw_output", "embedding", "text"})


class QaState(TypedDict, total=False):
    """The loop's checkpoint state: ids, counters and the answer payload.

    ``query`` and ``refined_query`` are the user's own words (bounded,
    small); ``chunk_ids`` references the in-run retrieval store;
    ``answer`` is the structured payload the API backend receives.
    Nothing else may be added: ``validate_state`` enforces the budget
    and the forbidden-key list on every run.
    """

    run_id: str
    tenant_id: str
    query: str
    refined_query: str
    filters: dict[str, str]
    chunk_ids: list[str]
    methods: list[str]
    candidate_count: int
    passes: int
    verdict: str
    low_confidence: bool
    answer: dict[str, Any]
    outcome: str


def effective_query(state: QaState) -> str:
    """The query the current pass retrieves with: refined when set."""
    return state.get("refined_query") or state["query"]


def validate_state(state: Mapping[str, Any]) -> None:
    """Refuse a state that breaches the checkpoint budget or carries text.

    The checkpoint budget is the platform's 64 KiB rule and the
    forbidden keys are its text/payload rule, applied to the RAG
    state so a prompt, a chunk or a raw model output can never be
    persisted by accident.
    """
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("RAG checkpoint exceeds the 64 KiB state limit")
    forbidden = FORBIDDEN_STATE_KEYS & set(state)
    if forbidden:
        raise ValueError(f"RAG state may not carry {sorted(forbidden)}")
    for key, value in state.items():
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError(f"RAG state field {key} exceeds the 4 KiB reference limit")


def _fused_score(chunk_id: str, fused: Mapping[str, float]) -> float:
    """The fused score clamped to [0, 1] and rounded for the state."""
    return round(min(1.0, max(0.0, fused.get(chunk_id, 0.0))), 6)


class RagLoop:
    """The agentic loop: ports wired into the LangGraph state machine.

    One instance serves one query — the retrieval store is in-run
    state, like the workflow's payload store, so a resumed run
    re-fetches chunk text through its retrievers instead of
    checkpointing it. ``build_graph`` returns the uncompiled graph
    so the deployment attaches its own checkpointer; ``answer``
    compiles a single-shot in-memory copy for the common path.
    """

    def __init__(
        self,
        config: RagConfig,
        *,
        subject: Subject,
        llm: LlmPort,
        embedder: EmbedderPort,
        guardrail: GuardrailPort,
        dense: DenseRetrieverPort | None = None,
        sparse: SparseRetrieverPort | None = None,
        graph: GraphRetrieverPort | None = None,
        reranker: RerankerPort | None = None,
        knowledge: KnowledgeGraph | None = None,
        judge: JudgePort | None = None,
        cache: SemanticCache | None = None,
        clock: Clock | None = None,
        review: bool = False,
        graph_hops: int = GRAPH_HOPS,
    ) -> None:
        """Wire the ports, the subject and the frozen configuration."""
        self.config = config
        self.subject = subject
        self.llm = llm
        self.embedder = embedder
        self.guardrail = guardrail
        self.dense = dense
        self.sparse = sparse
        self.graph = graph
        self.reranker = reranker
        self.knowledge = knowledge
        self.judge = judge
        self.cache = cache
        self.clock = clock
        self.review = review
        self.graph_hops = graph_hops
        self._chunks: dict[str, RetrievedChunk] = {}

    async def answer(
        self,
        query: str,
        *,
        filters: Mapping[str, str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the loop for one query and return the answer payload.

        The single-shot entry point: it compiles an in-memory graph,
        invokes it with the query and returns the answer payload the
        API backend receives. Deployments that need the human-review
        interrupt or durable resume compile ``build_graph`` with a
        checkpointer and drive the graph directly.
        """
        identifier = uuid4() if run_id is None else UUID(run_id)
        graph = self.build_graph().compile()
        state = await graph.ainvoke(
            {
                "run_id": str(identifier),
                "tenant_id": self.subject.tenant_id,
                "query": query,
                "filters": dict(filters or {}),
            }
        )
        validate_state(state)
        return state.get("answer") or answer_payload(refusal("the loop produced no answer"))

    def build_graph(self) -> Any:
        """Construct the agentic loop graph (uncompiled)."""
        graph = StateGraph(QaState)
        graph.add_node("guard", self._guard)
        graph.add_node("route", self._route)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("reflect", self._reflect)
        graph.add_node("generate", self._generate)
        graph.add_node("refuse", self._refuse)
        graph.add_node("review_gate", self._review_gate)
        graph.add_edge(START, "guard")
        graph.add_conditional_edges("guard", self._after_guard, {"route": "route", END: END})
        graph.add_edge("route", "retrieve")
        graph.add_edge("retrieve", "reflect")
        graph.add_conditional_edges(
            "reflect",
            self._after_reflect,
            {"generate": "generate", "retry": "route", "refuse": "refuse"},
        )
        graph.add_conditional_edges(
            "generate",
            self._after_generate,
            {"review_gate": "review_gate", "refuse": "refuse"},
        )
        graph.add_edge("refuse", END)
        graph.add_edge("review_gate", END)
        return graph

    # -- nodes ----------------------------------------------------------------

    async def _guard(self, state: QaState) -> dict[str, Any]:
        """Classify the query before any model sees it (OWASP LLM01)."""
        decision = await self.guardrail.classify(state["query"])
        log_event(
            "rag.guard",
            outcome="allow" if decision.allowed else "block",
            run_id=state.get("run_id"),
            tenant_id=state.get("tenant_id"),
        )
        if decision.allowed:
            return {}
        answer = refusal(decision.reason or "query blocked by the guardrails")
        return {"answer": answer_payload(answer), "outcome": "blocked"}

    async def _route(self, state: QaState) -> dict[str, Any]:
        """Choose the retrieval arms for the effective query."""
        plan = route_query(effective_query(state))
        return {"methods": list(plan.methods)}

    async def _retrieve(self, state: QaState) -> dict[str, Any]:
        """Run the planned arms, fuse, filter and rank the candidates."""
        query = effective_query(state)
        methods = tuple(state.get("methods") or ())
        filters = dict(state.get("filters") or {})
        config = self.config.retrieval
        trace_id = _trace_id(state)
        rankings: dict[str, tuple[str, ...]] = {}
        candidates: dict[str, RetrievedChunk] = {}
        with phase_span(
            trace_id,
            "retrieve",
            {
                "rag.retriever.top_k": config.top_k,
                "rag.retriever.methods": ",".join(methods),
            },
        ):
            if "dense" in methods:
                if self.dense is None:
                    raise NotConfigured("the dense retriever port is required by the plan")
                embedding = await self.embedder.embed(query)
                hits = await self.dense.search(embedding, config.top_k, filters)
                rankings["dense"] = tuple(hit.chunk_id for hit in hits)
                candidates.update({hit.chunk_id: hit for hit in hits})
            if "sparse" in methods:
                if self.sparse is None:
                    raise NotConfigured("the sparse retriever port is required by the plan")
                hits = await self.sparse.search(query, config.top_k, filters)
                rankings["sparse"] = tuple(hit.chunk_id for hit in hits)
                candidates.update({hit.chunk_id: hit for hit in hits})
            if "graph" in methods:
                if self.graph is None:
                    raise NotConfigured("the graph retriever port is required by the plan")
                seeds = (
                    extract_seed_entities(query, self.knowledge)
                    if self.knowledge is not None
                    else ()
                )
                hits = await self.graph.search(seeds, self.graph_hops)
                rankings["graph"] = tuple(hit.chunk_id for hit in hits)
                candidates.update({hit.chunk_id: hit for hit in hits})
            weights = {
                "dense": config.dense_weight,
                "sparse": config.sparse_weight,
                "graph": config.dense_weight,
            }
            fused = weighted_reciprocal_rank_fusion(rankings, weights, k=config.rrf_k)
            visible = filter_by_acl(candidates.values(), self.subject)
            filtered = metadata_filter(visible, filters, required=config.metadata_required)
            kept = drop_below(filtered, fused, config.min_score)
            if self.reranker is not None:
                ordered = tuple(await self.reranker.rerank(query, kept))
            else:
                ordered = rerank(
                    query,
                    kept,
                    fused=fused,
                    lexical_weight=config.lexical_weight,
                )
            selected = select_top_k(ordered, config.top_k)
            scored = tuple(
                chunk.model_copy(update={"score": _fused_score(chunk.chunk_id, fused)})
                for chunk in selected
            )
        self._chunks.update({chunk.chunk_id: chunk for chunk in scored})
        passes = state.get("passes", 0) + 1
        log_event(
            "rag.retrieve",
            phase="retrieve",
            outcome="ok",
            run_id=state.get("run_id"),
            tenant_id=state.get("tenant_id"),
            chunks=len(scored),
            methods=",".join(methods),
            top_k=config.top_k,
            loop_passes=passes,
        )
        return {
            "chunk_ids": [chunk.chunk_id for chunk in scored],
            "methods": list(methods),
            "candidate_count": len(candidates),
            "passes": passes,
        }

    async def _reflect(self, state: QaState) -> dict[str, Any]:
        """Critique the retrieved context and route the loop."""
        query = effective_query(state)
        chunks = self._context(state)
        judged = await self.judge.critique(query, chunks) if self.judge is not None else None
        verdict = evaluate(
            query,
            chunks,
            self.config.reflection,
            judge=self.judge,
            judged=judged,
        )
        passes = state.get("passes", 0)
        if passes >= self.config.loop.max_iterations:
            decision = "refuse"  # circuit breaker (OWASP LLM10)
        else:
            decision = decide(verdict, passes=passes, config=self.config.reflection)
        update: dict[str, Any] = {"verdict": decision}
        if decision == "retry" and verdict.refined_query is not None:
            update["refined_query"] = verdict.refined_query
        log_event(
            "rag.reflect",
            phase="reflect",
            outcome=decision,
            run_id=state.get("run_id"),
            tenant_id=state.get("tenant_id"),
            decision=decision,
            loop_passes=passes,
            chunks=len(chunks),
        )
        return update

    async def _generate(self, state: QaState) -> dict[str, Any]:
        """Answer from the retrieved context, with citation and safety checks."""
        query = effective_query(state)
        chunks = self._context(state)
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        trace_id = _trace_id(state)
        embedding: list[float] | None = None
        if self.cache is not None:
            embedding = await self.embedder.embed(query)
            cached = self.cache.lookup(embedding, now=self._now())
            log_event(
                "rag.cache",
                phase="generate",
                outcome="hit" if cached is not None else "miss",
                run_id=state.get("run_id"),
                tenant_id=state.get("tenant_id"),
            )
            if cached is not None:
                return {
                    "answer": answer_payload(cached),
                    "outcome": "cache_hit",
                }
        with phase_span(
            trace_id,
            "generate",
            {
                "gen_ai.request.model": GENERATOR_TIER,
            },
        ):
            prompt = build_prompt(query, chunks)
            result = await self.llm.complete(
                GENERATOR_TIER,
                SYSTEM_PROMPT,
                prompt,
                max_tokens=GENERATE_MAX_TOKENS,
            )
            answer = parse_answer(result.text)
            citations = tuple(
                Citation(
                    chunk_id=citation,
                    source_id=by_id[citation].source_id,
                )
                for citation in extract_citations(answer.final_answer)
                if citation in by_id
            )
            answer = answer.model_copy(update={"citations": citations})
            if orphan_citations(answer.final_answer, set(by_id)):
                # a citation to a chunk that was never retrieved is a
                # hallucination signature: fail closed, never deliver
                log_event(
                    "rag.generate",
                    phase="generate",
                    outcome="hallucination",
                    run_id=state.get("run_id"),
                    tenant_id=state.get("tenant_id"),
                )
                return {"outcome": "hallucination"}
            low = answer.confidence_score < self.config.answer.confidence_floor
            if low:
                answer = answer.model_copy(update={"requires_more_info": True})
            if self.cache is not None and embedding is not None and not answer.is_refusal:
                self.cache.store(embedding, answer, now=self._now())
        log_event(
            "rag.generate",
            phase="generate",
            outcome="answered",
            run_id=state.get("run_id"),
            tenant_id=state.get("tenant_id"),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_input_tokens=result.usage.cache_read_input_tokens,
        )
        return {
            "answer": answer_payload(answer),
            "outcome": "answered",
            "low_confidence": low,
        }

    async def _refuse(self, state: QaState) -> dict[str, Any]:
        """End the loop with the fail-closed refusal payload."""
        passes = state.get("passes", 0)
        if state.get("outcome") == "hallucination":
            reason = "the answer cited chunks that were not retrieved"
        elif state.get("verdict") == "refuse" and passes >= self.config.loop.max_iterations:
            reason = f"the retrieval loop exhausted {passes} passes"
        else:
            reason = "the retrieved context does not answer the query"
        answer = refusal(reason)
        log_event(
            "rag.refuse",
            phase="refuse",
            outcome="refused",
            run_id=state.get("run_id"),
            tenant_id=state.get("tenant_id"),
            loop_passes=passes,
        )
        return {"answer": answer_payload(answer), "outcome": "refused"}

    async def _review_gate(self, state: QaState) -> dict[str, Any]:
        """Human review for low-confidence answers (the interrupt node).

        The interrupt payload carries ids only. A reviewer who approves
        the answer lets it through unchanged; one who rejects it turns
        the answer into the refusal payload.
        """
        if not self.review or not state.get("low_confidence"):
            return {}
        decision = interrupt({"awaiting": "review", "run_id": state.get("run_id")})
        if decision:
            return {}
        answer = refusal("the answer was rejected in human review")
        return {"answer": answer_payload(answer), "outcome": "rejected"}

    # -- routing --------------------------------------------------------------

    def _after_guard(self, state: QaState) -> str:
        """Route around the loop when the guardrails blocked the query."""
        return END if state.get("outcome") == "blocked" else "route"

    def _after_reflect(self, state: QaState) -> str:
        """Route on the reflection verdict: generate, retry or refuse."""
        return state.get("verdict", "refuse")

    def _after_generate(self, state: QaState) -> str:
        """Route a hallucinated answer to the refusal node."""
        return "refuse" if state.get("outcome") == "hallucination" else "review_gate"

    # -- helpers --------------------------------------------------------------

    def _context(self, state: QaState) -> tuple[RetrievedChunk, ...]:
        """The retrieved chunks of the current pass, in retrieval order."""
        return tuple(
            self._chunks[chunk_id]
            for chunk_id in state.get("chunk_ids") or ()
            if chunk_id in self._chunks
        )

    def _now(self) -> float:
        """The injected clock as a POSIX timestamp (cache TTLs depend on it).

        The pure cache module takes ``now`` as a parameter; the loop
        supplies it from the injected clock, falling back to the wall
        clock only when no clock was wired in.
        """
        if self.clock is None:
            return time.time()
        return self.clock.now().timestamp()


def _trace_id(state: QaState) -> UUID:
    """The run id as a UUID: the trace id for the phase spans."""
    return UUID(str(state.get("run_id") or uuid4()))
