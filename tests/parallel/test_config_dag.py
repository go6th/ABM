"""Config and dependency graph (P2): the file is sourced and valid; every PRD edge exists and no
edge the sources do not support; cycles and unknown names are refused; coarsening keeps every
cross-body edge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from parallel.config import build, config_dir, load_parallel_config
from parallel.dag import body_of, coarsen, fine_graph, topological, validate
from parallel.errors import ConfigInvalid, DagInvalid

PULLS = {
    "pass1_relationship",
    "baseline",
    "news_pull",
    "jobs_board",
    "postings_12m",
    "tech_detections",
    "title_search",
    "review_communities",
    "people_levels",
    "posts_priority_a",
    "name_search",
    "platform_sentences",
    "tech_cross_check",
}
# Section 4.1 of the work package, statement by statement (dependency, dependent).
PRD_EDGES = {
    ("title_search", "people_merge"),  # merge by profile URL then name (step 7)
    ("people_merge", "association_search"),  # association search after the first merged pass
    ("people_merge", "profile_pull"),  # full profiles need the title-search results
    ("profile_pull", "people_levels"),  # levels and priority from the profiles
    ("association_search", "people_levels"),
    ("people_levels", "posts_priority_a"),  # posts only for priority A, after levels
    ("people_merge", "name_search"),  # person-name searches wait for the people list
    ("postings_12m", "platform_sentences"),  # platform sentences read from the postings
    ("postings_12m", "tech_cross_check"),  # the tech cross-check needs the postings
    ("tech_detections", "tech_cross_check"),
} | {(p, "lead_verification") for p in PULLS}  # verification after the pulls
ROOTS = {
    "pass1_relationship",
    "baseline",
    "news_pull",
    "jobs_board",
    "postings_12m",
    "tech_detections",
    "title_search",
    "review_communities",
}


def raw() -> dict:
    return yaml.safe_load((config_dir() / "parallel.yaml").read_bytes())


def test_parallel_config_loads_and_every_entry_is_sourced() -> None:
    cfg = load_parallel_config()
    assert len(cfg.config_hash) == 64
    assert cfg.providers.default_ceiling == 1  # concurrency 1 until measured (section 2.1)
    assert cfg.deadline_ceiling_s == 14400  # the sourced 4 h ceiling
    data = raw()
    for key in ("lanes", "seniority_passes", "name_search_categories", "sequential_tail"):
        assert {"source", "status"} & set(data[key]), key
    for section in ("providers", "fairness", "worker", "bodies"):
        assert {"source", "status"} & set(data[section]), section
    for name, entry in data["dynamic_caps"].items():
        assert {"source", "status"} & set(entry), name
    for name, entry in data["deadline"].items():
        assert {"source", "status"} & set(entry), name
    for name, item in data["unit_types"].items():
        assert item.get("basis"), name


def test_every_prd_edge_exists_and_no_unsupported_edge() -> None:
    cfg = load_parallel_config()
    assert fine_graph(cfg).edges() == PRD_EDGES
    roots = {n for n, deps in fine_graph(cfg).deps.items() if not deps}
    assert roots == ROOTS
    assert fine_graph(cfg).soft["lead_verification"] == frozenset(PULLS)
    assert cfg.sequential_tail == ("distill_grade", "build", "gate", "deliver")


def test_dag_rejects_a_cycle() -> None:
    with pytest.raises(DagInvalid, match="cycle"):
        topological({"a": ["b"], "b": ["c"], "c": ["a"]})
    data = raw()
    data["unit_types"]["title_search"]["depends_on"] = ["people_levels"]
    with pytest.raises(DagInvalid):
        validate(build(data, b"x"))


def test_dag_rejects_an_unknown_dependency() -> None:
    with pytest.raises(DagInvalid, match="unknown"):
        topological({"a": ["ghost"]})
    with pytest.raises(DagInvalid, match="itself"):
        topological({"a": ["a"]})
    data = raw()
    data["bodies"]["value"]["news"]["covers"] = ["news_pull", "ghost"]
    with pytest.raises(DagInvalid):
        validate(build(data, b"x"))


def test_a_unit_type_covered_by_two_bodies_or_none_is_refused() -> None:
    data = raw()
    data["bodies"]["value"]["baseline"]["covers"] = ["baseline", "news_pull"]
    with pytest.raises(DagInvalid, match="two bodies"):
        validate(build(data, b"x"))
    data = raw()
    data["bodies"]["value"]["baseline"]["covers"] = ["news_pull"]
    data["bodies"]["value"]["news"]["covers"] = ["news_pull"]
    with pytest.raises(DagInvalid):
        validate(build(data, b"x"))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("providers", "default_ceiling"), {"value": 1}, "no source or status"),
        (("providers", "floor"), 0, "floor"),
        (("fairness", "run_share_pct"), 101, "percentage"),
        (("unit_types", "baseline", "fan_out"), "everything", "fan_out"),
        (("unit_types", "baseline", "basis"), "", "basis"),
        (("providers", "heartbeat_s"), {"value": 500, "status": "proposal"}, "heartbeat"),
        (("dynamic_caps", "top_people"), {"value": 0, "status": "x"}, "top_people"),
    ],
)
def test_invalid_config_is_refused(path: tuple[str, ...], value: object, message: str) -> None:
    data = raw()
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ConfigInvalid, match=message):
        build(data, b"x")


def test_soft_dependency_must_be_a_dependency() -> None:
    data = raw()
    data["unit_types"]["baseline"]["soft"] = ["news_pull"]
    with pytest.raises(ConfigInvalid, match="soft"):
        build(data, b"x")


def test_unreadable_config_is_refused(tmp_path: Path) -> None:
    (tmp_path / "parallel.yaml").write_text("- a list")
    with pytest.raises(ConfigInvalid):
        load_parallel_config(tmp_path)
    with pytest.raises(ConfigInvalid):
        load_parallel_config(tmp_path / "missing")


def test_coarsened_graph_keeps_every_cross_body_edge() -> None:
    cfg = load_parallel_config()
    owner = body_of(cfg)
    fine = fine_graph(cfg)
    coarse = coarsen(fine, owner)
    for dep, node in fine.edges():
        if owner[dep] != owner[node]:
            assert owner[dep] in coarse.deps[owner[node]]
    for body, deps in coarse.deps.items():
        for d in deps:  # and no invented edge
            assert any(owner[a] == d and owner[b] == body for a, b in fine.edges())
    assert coarse.soft["verification"] == coarse.deps["verification"]
    assert coarse.deps["name_searches"] == {"people"}
    assert coarse.deps["tech"] == {"jobs"}


@st.composite
def dags(draw: st.DrawFn) -> dict[str, list[str]]:
    n = draw(st.integers(1, 12))
    names = [f"n{i}" for i in range(n)]
    return {
        name: draw(st.lists(st.sampled_from(names[:i]), unique=True)) if i else []
        for i, name in enumerate(names)
    }


@given(dags())
def test_topological_order_respects_every_edge(graph: dict[str, list[str]]) -> None:
    order = topological(graph)
    pos = {n: i for i, n in enumerate(order)}
    assert sorted(order) == sorted(graph)
    for node, deps in graph.items():
        assert all(pos[d] < pos[node] for d in deps)
