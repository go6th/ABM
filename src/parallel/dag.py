"""Load and validate the dependency graph (pure; P2: dependencies are data).

* ``validate`` refuses a cycle, a dependency on an unknown unit type, a soft dependency that is not
  a dependency, and a body set that does not cover every unit type exactly once;
* ``topological`` orders any graph deterministically (ties by name) or raises ``DagInvalid``;
* ``coarsen`` maps the fine unit-type graph onto the bodies that execute it: body A depends on body
  B when some unit type of A depends on some unit type of B; such a dependency is soft only when
  every underlying edge is soft. A body never depends on itself (its internal order is its own).

GUARANTEE(test_dag_rejects_a_cycle): cycles are refused.
GUARANTEE(test_dag_rejects_an_unknown_dependency): unknown names are refused.
GUARANTEE(test_coarsened_graph_keeps_every_cross_body_edge): no edge is lost by coarsening.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from parallel.config import ParallelConfig
from parallel.errors import DagInvalid


@dataclass(frozen=True)
class Graph:
    """Nodes with their hard and soft dependencies."""

    deps: Mapping[str, frozenset[str]]
    soft: Mapping[str, frozenset[str]]

    def edges(self) -> frozenset[tuple[str, str]]:
        """Every ``(dependency, dependent)`` pair."""
        return frozenset((d, n) for n, ds in self.deps.items() for d in ds)


def topological(deps: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Kahn's algorithm with name order for ties; ``DagInvalid`` on a cycle or unknown name."""
    incoming = {n: set(ds) for n, ds in deps.items()}
    for node, ds in incoming.items():
        unknown = ds - incoming.keys()
        if unknown:
            raise DagInvalid(f"{node} depends on unknown {sorted(unknown)}")
        if node in ds:
            raise DagInvalid(f"{node} depends on itself")
    order: list[str] = []
    # LOOP(topological order): cap=number of nodes (each pass removes at least one or raises);
    # exit=ordered, cycle
    while incoming:
        ready = sorted(n for n, ds in incoming.items() if not ds)
        if not ready:
            raise DagInvalid(f"dependency cycle among {sorted(incoming)}")
        for n in ready:
            order.append(n)
            del incoming[n]
        for ds in incoming.values():
            ds.difference_update(ready)
    return tuple(order)


def fine_graph(cfg: ParallelConfig) -> Graph:
    """The unit-type graph as configured."""
    return Graph(
        deps={n: frozenset(t.depends_on) for n, t in cfg.unit_types.items()},
        soft={n: frozenset(t.soft) for n, t in cfg.unit_types.items()},
    )


def body_of(cfg: ParallelConfig) -> dict[str, str]:
    """Unit type to the body that executes it (validated: exactly one)."""
    owner: dict[str, str] = {}
    for body in cfg.bodies.values():
        for unit_type in body.covers:
            if unit_type in owner:
                raise DagInvalid(f"unit type {unit_type} is covered by two bodies")
            owner[unit_type] = body.name
    return owner


def coarsen(graph: Graph, owner: Mapping[str, str]) -> Graph:
    """The body graph (see the module docstring)."""
    deps: dict[str, set[str]] = {b: set() for b in set(owner.values())}
    hard: dict[str, set[str]] = {b: set() for b in deps}
    for node, ds in graph.deps.items():
        for d in ds:
            a, b = owner[node], owner[d]
            if a == b:
                continue
            deps[a].add(b)
            if d not in graph.soft.get(node, frozenset()):
                hard[a].add(b)
    return Graph(
        deps={b: frozenset(ds) for b, ds in deps.items()},
        soft={b: frozenset(ds - hard[b]) for b, ds in deps.items()},
    )


def validate(cfg: ParallelConfig) -> tuple[str, ...]:
    """Every startup check of the module docstring; returns the fine topological order."""
    graph = fine_graph(cfg)
    order = topological(graph.deps)
    owner = body_of(cfg)
    uncovered = set(cfg.unit_types) - owner.keys()
    unknown = owner.keys() - set(cfg.unit_types)
    if uncovered or unknown:
        raise DagInvalid(f"bodies: uncovered={sorted(uncovered)} unknown={sorted(unknown)}")
    topological(coarsen(graph, owner).deps)
    if len(set(cfg.sequential_tail)) != len(cfg.sequential_tail):
        raise DagInvalid("the sequential tail repeats a stage")
    return order
