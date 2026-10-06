"""Ordered merge and source-code allocation (D-42, P1: order-independent outcomes).

Units run in parallel and finish in any order; their results are merged **by unit key**, never by
arrival, so the same inputs and the same provider responses give byte-identical state:

* ``merge_in_key_order(results, reducers)`` folds each unit's result map into one update, visiting
  units in key order and applying the field's reducer (the checkpoint state's own reducers are
  passed in by the workflow; a field without a reducer must be written by one unit only, else
  ``MergeConflict``);
* ``allocate_in_key_order(requests, allocate)`` gives source codes (``S-0001``) to the URLs units
  cited, in key order then citation order, each URL once, so codes never depend on which unit
  finished first. The workflow's merge step is the only caller in a parallel run; bodies that must
  allocate inside their own code (``allocates_sources`` in config) are chained in key order by the
  unit planner so they never run concurrently.

GUARANTEE(test_merge_is_independent_of_arrival_order): property test.
GUARANTEE(test_source_codes_are_independent_of_completion_order): property test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from parallel.errors import MergeConflict

Reducer = Callable[[Any, Any], Any]


def merge_in_key_order(
    results: Mapping[str, Mapping[str, Any]],
    reducers: Mapping[str, Reducer],
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One update from many unit results (see the module docstring); ``base`` (for example the
    phase's input state) is folded first."""
    merged: dict[str, Any] = dict(base or {})
    owner: dict[str, str] = {k: "<base>" for k in merged}
    for key in sorted(results):
        for field, value in results[key].items():
            if field not in merged:
                merged[field] = value
                owner[field] = key
                continue
            reducer = reducers.get(field)
            if reducer is None:
                if merged[field] != value:
                    raise MergeConflict(f"{field} written by {owner[field]} and {key}")
                continue
            try:
                merged[field] = reducer(merged[field], value)
            except ValueError as exc:
                raise MergeConflict(f"{field}: {exc}") from exc
    return merged


async def allocate_in_key_order(
    requests: Mapping[str, Sequence[str]], allocate: Callable[[str], Awaitable[str]]
) -> dict[str, str]:
    """Source codes for every requested URL, allocated in unit-key then citation order."""
    codes: dict[str, str] = {}
    for key in sorted(requests):
        for url in requests[key]:
            if url not in codes:
                codes[url] = await allocate(url)
    return codes
