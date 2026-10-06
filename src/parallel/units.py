"""Build the work units of a run from config, run input and stored rows (pure, capped; P3).

* Lanes come from config intersected with the run's in-scope lanes, seniority passes from config,
  dynamic sets (senior people, priority A people, the top people) from **stored rows** the caller
  read, each truncated to its hard cap with the number dropped reported (never silent), and the
  whole plan refused above ``units_per_run`` (``FanOutCapExceeded``).
* A dynamic unit type whose stored set is not available yet (its generating dependency has not
  finished) is represented by one ``expand`` placeholder; its dependents wait for it, so nothing
  that needs the people list can start before the people list exists (P2).
* Keys are deterministic: ``<unit type>|<lane or ->|<shard or ->`` for fine units and
  ``body|<name>`` for bodies. Rebuilding with more stored rows only adds units; existing keys and
  their dependencies never change, except that a placeholder is replaced by its expansion.
* Bodies that allocate source codes in their own code are chained in key order (each depends on
  the previous one), so allocations never interleave (P1).
* A model may suggest a gap; ``accept_suggestion`` maps it onto an existing unit key or raises
  ``UnitNotAllowed``. It can never create or alter a unit.

GUARANTEE(test_units_never_exceed_their_caps): every dynamic set is capped.
GUARANTEE(test_a_model_suggested_unit_outside_the_plan_is_refused): P3.
GUARANTEE(test_unit_keys_are_stable_across_rebuilds): adding stored rows adds units only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from parallel.config import ParallelConfig
from parallel.dag import body_of, coarsen, fine_graph
from parallel.errors import FanOutCapExceeded, UnitNotAllowed
from parallel.models import WorkUnit

EXPAND = "expand"  # body name of placeholder units (unpaid, executed by the planner shell)
DYNAMIC_CAP = {
    "senior_people": "senior_people",
    "priority_a_people": "priority_a_people",
    "top_people_x_category": "top_people",
}


@dataclass(frozen=True)
class UnitPlan:
    """The units of a run and what each dynamic set dropped to stay under its cap."""

    units: tuple[WorkUnit, ...]
    truncated: Mapping[str, int]

    def by_key(self) -> dict[str, WorkUnit]:
        """Units indexed by key."""
        return {u.key: u for u in self.units}


def unit_key(unit_type: str, lane: str | None = None, shard: str | None = None) -> str:
    """The deterministic key of a fine unit."""
    return f"{unit_type}|{lane or '-'}|{shard or '-'}"


def placeholder_key(unit_type: str) -> str:
    """The key of the expansion placeholder of a dynamic unit type."""
    return f"{unit_type}|*|*"


def _instances(
    cfg: ParallelConfig,
    unit_type: str,
    lanes: Sequence[str],
    stored: Mapping[str, Sequence[str]],
    truncated: dict[str, int],
) -> list[tuple[str | None, str | None]] | None:
    """``(lane, shard)`` pairs for one unit type, or None when its stored set is not available."""
    fan_out = cfg.unit_types[unit_type].fan_out
    if fan_out == "once":
        return [(None, None)]
    if fan_out == "lane":
        return [(lane, None) for lane in lanes]
    if fan_out == "lane_pass":
        return [(lane, str(p)) for lane in lanes for p in cfg.seniority_passes]
    name = fan_out.removeprefix("rows:")
    cap_key = DYNAMIC_CAP.get(name)
    if cap_key is None:
        raise UnitNotAllowed(f"unknown stored set {name}")
    source = "top_people" if name == "top_people_x_category" else name
    if source not in stored:
        return None
    rows = list(dict.fromkeys(str(r) for r in stored[source]))  # stable, de-duplicated
    cap = cfg.dynamic_caps[cap_key]
    if len(rows) > cap:
        truncated[name] = len(rows) - cap
        rows = rows[:cap]
    if name == "top_people_x_category":
        return [(None, f"{p}:{c}") for p in rows for c in cfg.name_search_categories]
    return [(None, r) for r in rows]


def fine_plan(
    cfg: ParallelConfig,
    *,
    lanes_in_scope: Sequence[str] | None = None,
    stored: Mapping[str, Sequence[str]] | None = None,
    high: frozenset[str] = frozenset(),
    est_cost_micros: Mapping[str, int] | None = None,
) -> UnitPlan:
    """One unit per instance of each unit type (the PRD-level DAG; the simulation runs this)."""
    lanes = [x for x in cfg.lanes if lanes_in_scope is None or x in lanes_in_scope]
    stored = stored or {}
    costs = est_cost_micros or {}
    truncated: dict[str, int] = {}
    keys_of: dict[str, list[str]] = {}
    pending: list[tuple[str, str | None, str | None, bool]] = []
    for unit_type in sorted(cfg.unit_types):
        inst = _instances(cfg, unit_type, lanes, stored, truncated)
        if inst is None:
            keys_of[unit_type] = [placeholder_key(unit_type)]
            pending.append((unit_type, None, None, True))
            continue
        keys_of[unit_type] = [unit_key(unit_type, lane, shard) for lane, shard in inst]
        pending.extend((unit_type, lane, shard, False) for lane, shard in inst)
    units: list[WorkUnit] = []
    for unit_type, lane, shard, is_placeholder in pending:
        spec = cfg.unit_types[unit_type]
        deps = tuple(sorted(k for d in spec.depends_on for k in keys_of[d]))
        soft = frozenset(k for d in spec.soft for k in keys_of[d])
        units.append(
            WorkUnit(
                key=placeholder_key(unit_type)
                if is_placeholder
                else unit_key(unit_type, lane, shard),
                unit_type=unit_type,
                body=EXPAND if is_placeholder else unit_type,
                lane=lane,
                shard=shard,
                depends_on=deps,
                soft_depends_on=soft,
                routes=()
                if is_placeholder or spec.provider_route is None
                else (spec.provider_route,),
                priority=1 if unit_type in high else 0,
                est_cost_micros=None if is_placeholder else costs.get(unit_type),
            )
        )
    if len(units) > cfg.dynamic_caps["units_per_run"]:
        raise FanOutCapExceeded(f"{len(units)} units exceed units_per_run")
    return UnitPlan(tuple(sorted(units, key=lambda u: u.key)), truncated)


def body_plan(
    cfg: ParallelConfig,
    *,
    high: frozenset[str] = frozenset(),
    est_cost_micros: Mapping[str, int] | None = None,
) -> UnitPlan:
    """One composite unit per body over the coarsened DAG (what the engine executes today)."""
    owner = body_of(cfg)
    graph = coarsen(fine_graph(cfg), owner)
    costs = est_cost_micros or {}
    allocators = sorted(n for n, b in cfg.bodies.items() if b.allocates_sources)
    chain = {b: allocators[i - 1] for i, b in enumerate(allocators) if i > 0}
    units = []
    for name in sorted(cfg.bodies):
        body = cfg.bodies[name]
        routes = sorted(
            {r for t in body.covers if (r := cfg.unit_types[t].provider_route) is not None}
        )
        units.append(
            WorkUnit(
                key=f"body|{name}",
                unit_type=name,
                body=name,
                depends_on=tuple(
                    sorted(
                        {f"body|{d}" for d in graph.deps[name]}
                        | ({f"body|{chain[name]}"} if name in chain else set())
                    )
                ),
                soft_depends_on=frozenset(f"body|{d}" for d in graph.soft[name]),
                routes=tuple(routes),
                priority=1 if name in high else 0,
                est_cost_micros=costs.get(name),
                covers=body.covers,
            )
        )
    return UnitPlan(tuple(units), {})


def accept_suggestion(plan: UnitPlan, unit_type: str, lane: str | None, shard: str | None) -> str:
    """A model's gap suggestion may only point at a unit the plan already has (P3)."""
    key = unit_key(unit_type, lane, shard)
    if key not in plan.by_key():
        raise UnitNotAllowed(f"{key} is not in the run's plan")
    return key
