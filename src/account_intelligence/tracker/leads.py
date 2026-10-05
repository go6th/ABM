"""Tracker Lead verification (T13): a thin adapter over ``account_intelligence.grading.leads``.

The rule (three differently worded queries across two tool categories, PRD 3.4) has one
implementation, in the grading layer; this module only converts tracker call records. The
tracker config's ``lead_proof`` numbers are superseded by ``config/grading.yaml`` and the
similarity proposal in ``config/grading_policy.yaml`` (ADR-0013).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.leads import (
    account_terms,
    new_lead,
    query_tokens,
    similarity,
    submit_query,
)
from account_intelligence.grading.ports import FetchRecord
from account_intelligence.tracker.model import CallRecord

_NIL = UUID(int=0)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def is_differently_worded(q1: str, q2: str, threshold: float | None = None) -> bool:
    """True when the two queries' token sets are below the configured similarity threshold."""
    cfg = load_grading_config()
    limit = cfg.similarity.max_similarity_exclusive if threshold is None else threshold
    return similarity(query_tokens(q1, (), cfg), query_tokens(q2, (), cfg)) < limit


def verify_lead_proof(
    calls: list[CallRecord], account_domain: str = "", account_name: str | None = None
) -> bool:
    """True when the calls prove the Lead rule (grading.leads.evaluate semantics)."""
    cfg = load_grading_config()
    fetches = {
        c.call_ref: FetchRecord(
            c.call_ref,
            _NIL,
            "",
            "",
            "search_snippet",
            c.tool_category,
            _EPOCH,
            c.success,
            c.queries,
        )
        for c in calls
    }
    terms = account_terms(account_domain, account_name) if account_domain else frozenset()
    state = new_lead("tracker")
    for c in calls:
        for q in c.queries:
            if state.status != "pending":
                break
            state = submit_query(state, q, c.tool_category, c.call_ref, fetches, terms, cfg)
    return state.status == "complete"
