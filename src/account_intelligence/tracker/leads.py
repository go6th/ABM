"""Tracker lead verification rule (T13)."""

from __future__ import annotations

import difflib

from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.model import CallRecord


def is_differently_worded(q1: str, q2: str, threshold: float) -> bool:
    """Returns True if similarity is below threshold."""
    if not q1 or not q2:
        return True
    seq = difflib.SequenceMatcher(None, q1.lower(), q2.lower())
    return seq.ratio() < threshold


def verify_lead_proof(calls: list[CallRecord]) -> bool:
    config = load_tracker_config()
    lead_rules = config.d_13_tracker_rules.get("lead_proof", {})
    min_queries = lead_rules.get("min_queries", 3)
    min_tools = lead_rules.get("min_tool_categories", 2)
    threshold = lead_rules.get("similarity_threshold", {}).get("value", 0.8)

    unique_queries: list[str] = []
    tools = set()

    for c in calls:
        if not c.success:
            continue
        tools.add(c.tool_category)
        for q in c.queries:
            # Check if q is differently worded from all unique_queries
            if all(is_differently_worded(q, u, threshold) for u in unique_queries):
                unique_queries.append(q)

    return len(unique_queries) >= int(min_queries) and len(tools) >= int(min_tools)
