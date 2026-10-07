"""Agentic RAG subsystem: hybrid retrieval, graph traversal, reflection and a
bounded LangGraph question-answering loop with strict citation provenance.

Modules are layered by dependency direction: ``models`` and ``config`` hold the
typed vocabulary; ``retrieval``, ``graphrag``, ``cache``, ``reflection``,
``prompt``, ``metrics``, ``synthetic``, ``guardrails`` and ``access`` are pure
logic over that vocabulary; ``obs`` adds OpenTelemetry; ``graph`` assembles the
LangGraph state machine from ports. Every external dependency is a port so the
pure core stays testable without a model, a vector store or Redis.
"""
