"""GraphRAG: an entity/relation graph and multi-hop traversal.

The knowledge graph models entities (people, organizations,
concepts) as nodes and their relationships as edges. A vector
store cannot connect two documents that are only loosely similar
to the query; graph traversal natively walks the edges and
returns an explicit, explainable evidence chain (the 54-point
accuracy gap on multi-hop reasoning in the blueprint).

Routing is deterministic and inspectable: relationship-shaped
questions (who, connected, between, via...) route to the graph,
keyword-shaped questions (error codes, ids, exact phrases) to
the sparse arm, and everything else to the dense arm — HybridRAG
uses the vector path for fact lookups and the graph for
relationship-driven queries.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

_RELATIONSHIP_HINTS = (
    "who",
    "whom",
    "connected",
    "connection",
    "relationship",
    "related",
    "between",
    "via",
    "through",
    "reports to",
    "works with",
    "partner",
    "affiliated",
    "subsidiary",
    "parent",
)
_KEYWORD_HINTS = ("error", "code", "id ", "id:", "http", "status", "trace")


@dataclass(frozen=True)
class Entity:
    """A graph node: an id and a kind (person, organization, concept)."""

    id: str
    kind: str


@dataclass(frozen=True)
class Relation:
    """A directed edge with a label (the explainable evidence link)."""

    source: str
    target: str
    label: str


@dataclass(frozen=True)
class KnowledgeGraph:
    """The entity graph: nodes, edges and traversal primitives."""

    entities: Mapping[str, Entity] = field(default_factory=dict)
    relations: tuple[Relation, ...] = ()

    def neighbors(self, entity_id: str) -> tuple[tuple[Entity, Relation], ...]:
        """Outgoing edges of ``entity_id`` with their target entities."""
        steps: list[tuple[Entity, Relation]] = []
        for relation in self.relations:
            if relation.source != entity_id:
                continue
            target = self.entities.get(relation.target)
            if target is not None:
                steps.append((target, relation))
        return tuple(steps)

    def multi_hop(self, seeds: Iterable[str], max_hops: int) -> tuple[tuple[str, int], ...]:
        """Breadth-first traversal from ``seeds``, at most ``max_hops`` deep.

        Returns ``(entity_id, hop_count)`` pairs in first-reached order.
        A ``max_hops`` of 0 returns the seeds themselves; a negative
        value is a configuration error, not an empty result.
        """
        if max_hops < 0:
            raise ValueError("max_hops cannot be negative")
        seen: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            if seed in self.entities and seed not in seen:
                seen[seed] = 0
                queue.append((seed, 0))
        while queue:
            entity_id, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for target, _ in self.neighbors(entity_id):
                if target.id not in seen:
                    seen[target.id] = depth + 1
                    queue.append((target.id, depth + 1))
        return tuple(seen.items())

    def evidence_path(
        self, start: str, goal: str, *, max_hops: int = 8
    ) -> tuple[Relation, ...] | None:
        """The shortest edge path from ``start`` to ``goal``.

        The path is the explainable evidence chain: the answer can
        cite each hop, not an opaque similarity score. ``None`` when
        no path exists within ``max_hops``.
        """
        if max_hops < 0:
            raise ValueError("max_hops cannot be negative")
        if start == goal and start in self.entities:
            return ()
        queue: deque[tuple[str, tuple[Relation, ...]]] = deque()
        queue.append((start, ()))
        visited = {start}
        while queue:
            entity_id, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for target, relation in self.neighbors(entity_id):
                if target.id in visited:
                    continue
                extended = (*path, relation)
                if target.id == goal:
                    return extended
                visited.add(target.id)
                queue.append((target.id, extended))
        return None


@dataclass(frozen=True)
class RetrievalPlan:
    """The router's decision: which arms to run and why."""

    methods: tuple[str, ...]
    rationale: str


def route_query(query: str) -> RetrievalPlan:
    """Choose the retrieval arms for ``query`` (the routing heuristic).

    Relationship-shaped questions get the graph plus the dense arm
    (seed extraction still needs semantics); keyword-shaped questions
    get the sparse arm (ids and codes defeat embeddings); everything
    else is a fact lookup on the dense arm. Applying agentic
    retrieval selectively — a single pass when the shape allows it —
    is the cost-control rule of the blueprint.
    """
    folded = query.casefold()
    if any(hint in folded for hint in _RELATIONSHIP_HINTS):
        return RetrievalPlan(
            methods=("graph", "dense"),
            rationale="relationship-shaped query routes to the graph",
        )
    if any(hint in folded for hint in _KEYWORD_HINTS):
        return RetrievalPlan(
            methods=("sparse",),
            rationale="keyword-shaped query routes to the sparse arm",
        )
    return RetrievalPlan(
        methods=("dense",),
        rationale="fact lookup routes to the dense arm",
    )


def extract_seed_entities(query: str, graph: KnowledgeGraph) -> tuple[str, ...]:
    """Entities of ``graph`` mentioned in ``query`` (case-insensitive)."""
    folded = query.casefold()
    return tuple(
        entity_id
        for entity_id, entity in sorted(graph.entities.items())
        if entity.id.casefold() in folded or entity.kind.casefold() in folded
    )


class GraphRetrieverPort(Protocol):
    """The graph arm: connected chunks for seed entities."""

    async def search(self, seeds: Sequence[str], max_hops: int) -> list[Any]:
        """Return chunks reachable from ``seeds`` within ``max_hops``."""
        ...
