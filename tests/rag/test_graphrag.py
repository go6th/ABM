"""GraphRAG: entity graph traversal, evidence paths and query routing."""

from __future__ import annotations

import pytest

from account_intelligence.rag.graphrag import (
    Entity,
    KnowledgeGraph,
    Relation,
    extract_seed_entities,
    route_query,
)


def _graph() -> KnowledgeGraph:
    """A small org chart: acme -> acme eu -> acme eu finance."""
    return KnowledgeGraph(
        entities={
            "acme": Entity(id="acme", kind="organization"),
            "acme-eu": Entity(id="acme-eu", kind="organization"),
            "finance": Entity(id="finance", kind="team"),
            "ada": Entity(id="ada", kind="person"),
        },
        relations=(
            Relation(source="acme", target="acme-eu", label="subsidiary"),
            Relation(source="acme-eu", target="finance", label="has_team"),
            Relation(source="finance", target="ada", label="member"),
        ),
    )


class TestKnowledgeGraph:
    def test_neighbors(self) -> None:
        """Outgoing edges come back with their target entities."""
        graph = _graph()
        steps = graph.neighbors("acme")
        assert [target.id for target, _ in steps] == ["acme-eu"]
        assert steps[0][1].label == "subsidiary"

    def test_neighbors_of_unknown_entity(self) -> None:
        """An unknown entity has no neighbours."""
        assert _graph().neighbors("ghost") == ()

    def test_multi_hop_depths(self) -> None:
        """Breadth-first traversal records first-reached depths."""
        graph = _graph()
        reached = dict(graph.multi_hop(["acme"], max_hops=3))
        assert reached == {"acme": 0, "acme-eu": 1, "finance": 2, "ada": 3}

    def test_multi_hop_zero_hops_returns_seeds(self) -> None:
        """Zero hops means the seeds themselves, at depth 0."""
        graph = _graph()
        assert dict(graph.multi_hop(["acme", "ada"], max_hops=0)) == {
            "acme": 0,
            "ada": 0,
        }

    def test_multi_hop_skips_unknown_seeds(self) -> None:
        """Seeds that are not entities are ignored, not fatal."""
        assert dict(_graph().multi_hop(["ghost"], max_hops=2)) == {}

    def test_multi_hop_negative_raises(self) -> None:
        """A negative hop budget is a configuration error."""
        with pytest.raises(ValueError):
            _graph().multi_hop(["acme"], max_hops=-1)

    def test_evidence_path(self) -> None:
        """The shortest edge path is the explainable evidence chain."""
        path = _graph().evidence_path("acme", "ada")
        assert path is not None
        assert [relation.label for relation in path] == [
            "subsidiary",
            "has_team",
            "member",
        ]

    def test_evidence_path_self_is_empty(self) -> None:
        """A path from an entity to itself needs no edges."""
        assert _graph().evidence_path("acme", "acme") == ()

    def test_evidence_path_none_when_unreachable(self) -> None:
        """No path within the hop budget returns None."""
        assert _graph().evidence_path("ada", "acme") is None
        assert _graph().evidence_path("acme", "ada", max_hops=1) is None

    def test_evidence_path_negative_raises(self) -> None:
        """A negative hop budget is a configuration error."""
        with pytest.raises(ValueError):
            _graph().evidence_path("acme", "ada", max_hops=-1)


class TestRouting:
    def test_relationship_query_routes_to_graph(self) -> None:
        """Who/connected questions take the graph plus the dense arm."""
        plan = route_query("who does acme report to?")
        assert plan.methods == ("graph", "dense")

    def test_keyword_query_routes_to_sparse(self) -> None:
        """Ids, codes and error messages defeat embeddings."""
        plan = route_query("what is http status code 500?")
        assert plan.methods == ("sparse",)

    def test_fact_lookup_routes_to_dense(self) -> None:
        """Everything else is a fact lookup on the vector path."""
        plan = route_query("what is the annual revenue of acme?")
        assert plan.methods == ("dense",)

    def test_routing_is_case_insensitive(self) -> None:
        """Routing hints match regardless of case."""
        assert route_query("WHO does ACME report to?").methods == ("graph", "dense")


class TestSeedExtraction:
    def test_extracts_by_id_and_kind(self) -> None:
        """Entities mentioned by id or kind seed the traversal."""
        graph = _graph()
        assert extract_seed_entities("what does acme do", graph) == ("acme",)
        assert extract_seed_entities("tell me about a person", graph) == ("ada",)

    def test_no_seeds_for_unknown_text(self) -> None:
        """Text that names no entity yields no seeds."""
        assert extract_seed_entities("nothing relevant here", _graph()) == ()
