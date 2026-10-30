"""The ``ask.route`` log line says how many table columns and values the question matched, never
which ones (leakage review 2026-10-30, F9): the values are the caller's own uploaded table cells
(company names, platforms, possibly people), and the router's reason repeats them. Logs carry ids
and counts, not text.

GUARANTEE(test_the_route_log_carries_counts_and_never_the_cells)
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from account_intelligence.api.routes.ask import route_log
from account_intelligence.replica.router import Decision

ROOT = Path(__file__).resolve().parents[2]


def test_the_route_log_carries_counts_and_never_the_cells() -> None:
    decision = Decision(
        route="tabular",
        reason="filter words ['show'], columns ['company'], values ['acme holdings', 'jane doe']",
        matched_columns=("company", "owner"),
        matched_values=("acme holdings", "jane doe", "veeva"),
    )
    fields = route_log(decision)
    assert fields == {"matched_column_count": 2, "matched_value_count": 3}
    blob = json.dumps(fields)
    assert "acme" not in blob and "jane" not in blob and "company" not in blob


def test_the_log_allow_list_names_the_counts_and_not_the_texts() -> None:
    fields = yaml.safe_load((ROOT / "config/telemetry/logs.yaml").read_text())["fields"]
    assert {"matched_column_count", "matched_value_count"} <= set(fields)
    assert "matched_columns" not in fields and "matched_values" not in fields
