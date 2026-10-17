"""Metrics of the product screens' answers (an ``obs.py`` edge module: the only place in the
package that acquires a meter). Both metrics are in ``config/telemetry/metrics.yaml``.

* ``ask_routes_total{route}``: how Ask turns were routed (tabular, prose, mixed): the share of
  tabular turns is ``ask_routes_total{route="tabular"}`` over the sum;
* ``ask_table_queries_total{empty_result}``: ``query_table`` calls, by whether they returned
  no row (the empty-result rate);
* ``ask_run_python_total{sandbox_outcome}``: ``run_python`` sandbox runs, by whether the code ran
  (``ok``) or came back a structured error — the sandbox-run rate against the turns.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("account_intelligence.replica")
_routes = _meter.create_counter("ask_routes_total", description="Ask turns by route")
_queries = _meter.create_counter(
    "ask_table_queries_total", description="query_table calls by whether they found rows"
)
_python = _meter.create_counter(
    "ask_run_python_total", description="run_python sandbox runs by outcome"
)


def record_route(route: str) -> None:
    """One Ask turn routed ``route``."""
    _routes.add(1, {"route": route})


def record_table_queries(calls: int, empty: int) -> None:
    """The turn's ``query_table`` calls: ``empty`` of ``calls`` found no row."""
    if empty:
        _queries.add(empty, {"empty_result": "true"})
    if calls - empty > 0:
        _queries.add(calls - empty, {"empty_result": "false"})


def record_run_python(outcome: str) -> None:
    """One ``run_python`` sandbox run ("ok" or "error")."""
    _python.add(1, {"sandbox_outcome": outcome})
