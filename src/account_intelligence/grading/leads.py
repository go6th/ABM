"""Lead verification (PRD 3.4; G9). The single implementation; the tracker and the
workflow verification stage call ``evaluate_lead``.

"A lead is not dead after one search: at least three differently worded queries across at
least two tools before anything is marked unverified." (PRD 3.4). The numbers come from
``grading.yaml`` (``stays_lead_only_if``). A query counts only if:

* its call is a **successful** call in this run's ledger, and the query text is one the
  call actually sent (the caller cannot invent a query);
* its tool category is the ledger's category for that call and is a routing category;
* its normalised token set (NFKC, lowercase, punctuation stripped, stopwords and account
  tokens removed) has similarity below the configured threshold against **every** query
  already accepted for this Lead (reordering, case, punctuation or adding the account name
  do not make a query different);
* accepting it still leaves the category requirement reachable within the query budget.

Bounded: at most ``min_distinct_queries`` accepted, at most ``max_rejected_proposals``
rejected per pass (then ``incomplete(no_distinct_query)``), at most ``max_repass`` re-passes.
Pure: no I/O, no clock.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.errors import LeadRuleViolation
from account_intelligence.grading.ports import FetchRecord

LeadStatus = Literal["pending", "complete", "upgraded", "incomplete"]
ExitReason = Literal[
    "converged", "cap_reached", "no_progress", "budget", "deadline", "source_blocked", "upgraded"
]


@dataclass(frozen=True)
class QueryRecord:
    """One offered verification query and whether it was accepted."""

    text: str
    tool_category: str
    call_ref: str
    accepted: bool
    rejection_reason: str | None
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class LeadVerification:
    """A Lead's verification state (pending, complete, upgraded, incomplete)."""

    lead_key: str
    status: LeadStatus = "pending"
    queries: tuple[QueryRecord, ...] = ()
    reason: str | None = None
    exit_reason: ExitReason | None = None
    repasses_used: int = 0
    rejected_this_pass: int = 0
    upgraded_by: str | None = None

    @property
    def accepted(self) -> tuple[QueryRecord, ...]:
        """The accepted queries."""
        return tuple(q for q in self.queries if q.accepted)

    @property
    def categories(self) -> frozenset[str]:
        """Tool categories of the accepted queries."""
        return frozenset(q.tool_category for q in self.accepted)


_WORD = re.compile(r"\w+")


def account_terms(account_domain: str, account_name: str | None = None) -> frozenset[str]:
    """Tokens of the account's name and domain labels (adding them never makes a query new)."""
    labels = [p for p in account_domain.lower().split(".")[:-1] if p and p != "www"]
    words = _WORD.findall(unicodedata.normalize("NFKC", account_name or "").lower())
    return frozenset(labels + words)


def query_tokens(text: str, terms: Iterable[str], cfg: GradingConfig) -> frozenset[str]:
    """Normalised token set of a query (stopwords and account terms removed)."""
    words = _WORD.findall(unicodedata.normalize("NFKC", text).lower())
    drop = set(cfg.similarity.stopwords) | set(terms)
    return frozenset(w for w in words if w not in drop)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of token sets; two empty sets are identical (1.0)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def new_lead(lead_key: str) -> LeadVerification:
    """A fresh pending Lead."""
    return LeadVerification(lead_key=lead_key)


def _reject(
    state: LeadVerification,
    text: str,
    category: str,
    call_ref: str,
    reason: str,
    cfg: GradingConfig,
) -> LeadVerification:
    record = QueryRecord(text, category, call_ref, False, reason, ())
    rejected = state.rejected_this_pass + 1
    nxt = replace(state, queries=(*state.queries, record), rejected_this_pass=rejected)
    if rejected >= cfg.similarity.max_rejected_proposals:
        return replace(
            nxt, status="incomplete", reason="no_distinct_query", exit_reason="cap_reached"
        )
    return nxt


def submit_query(
    state: LeadVerification,
    query: str,
    tool_category: str,
    call_ref: str,
    fetches: Mapping[str, FetchRecord],
    terms: Iterable[str],
    cfg: GradingConfig,
) -> LeadVerification:
    """Offer one verification query. Returns the new state; the last query record says
    whether it was accepted and why not. A non-pending Lead accepts nothing."""
    if state.status != "pending":
        raise LeadRuleViolation(f"lead is {state.status}; queries are no longer accepted")
    fetch = fetches.get(call_ref)
    if fetch is None:
        return _reject(state, query, tool_category, call_ref, "call_not_in_ledger", cfg)
    if not fetch.success:
        return _reject(state, query, tool_category, call_ref, "call_failed", cfg)
    if fetch.tool_category != tool_category:
        return _reject(state, query, tool_category, call_ref, "category_mismatch", cfg)
    if tool_category not in cfg.tool_categories:
        return _reject(state, query, tool_category, call_ref, "category_unknown", cfg)
    sent = {" ".join(q.split()) for q in fetch.queries}
    if " ".join(query.split()) not in sent:
        return _reject(state, query, tool_category, call_ref, "query_not_sent_by_call", cfg)
    tokens = query_tokens(query, terms, cfg)
    if not tokens:
        return _reject(state, query, tool_category, call_ref, "empty_after_normalisation", cfg)
    threshold = cfg.similarity.max_similarity_exclusive
    for prior in state.accepted:
        if similarity(tokens, frozenset(prior.tokens)) >= threshold:
            return _reject(state, query, tool_category, call_ref, "near_duplicate", cfg)
    rule = cfg.lead_rule
    slots_after = rule.min_distinct_queries - len(state.accepted) - 1
    cats_after = len(state.categories | {tool_category})
    if rule.min_distinct_tool_categories - cats_after > slots_after:
        return _reject(state, query, tool_category, call_ref, "needs_new_category", cfg)
    record = QueryRecord(query, tool_category, call_ref, True, None, tuple(sorted(tokens)))
    nxt = replace(state, queries=(*state.queries, record))
    if (
        len(nxt.accepted) >= rule.min_distinct_queries
        and len(nxt.categories) >= rule.min_distinct_tool_categories
    ):
        return replace(nxt, status="complete", exit_reason="converged")
    return nxt


def mark_upgraded(
    state: LeadVerification, call_ref: str, fetches: Mapping[str, FetchRecord]
) -> LeadVerification:
    """A verification call produced a supporting source: the claim must be re-graded through
    the normal pipeline (never edited by hand)."""
    fetch = fetches.get(call_ref)
    if fetch is None or not fetch.success:
        raise LeadRuleViolation("an upgrade needs a successful call in this run's ledger")
    return replace(state, status="upgraded", upgraded_by=call_ref, exit_reason="upgraded")


def mark_incomplete(
    state: LeadVerification, reason: Literal["budget", "source_blocked", "deadline"]
) -> LeadVerification:
    """Park a Lead as incomplete with its reason (budget, blocked source, deadline)."""
    exit_reason: ExitReason = (
        "budget" if reason == "budget" else "deadline" if reason == "deadline" else "source_blocked"
    )
    return replace(state, status="incomplete", reason=reason, exit_reason=exit_reason)


def repass(state: LeadVerification, cfg: GradingConfig) -> LeadVerification:
    """One re-pass of a parked (incomplete) Lead when later evidence supports it."""
    if state.status != "incomplete":
        raise LeadRuleViolation("only an incomplete lead can be re-passed")
    if state.repasses_used >= cfg.similarity.max_repass:
        raise LeadRuleViolation("re-pass budget used")
    return replace(
        state,
        status="pending",
        reason=None,
        exit_reason=None,
        repasses_used=state.repasses_used + 1,
        rejected_this_pass=0,
    )


def evaluate_lead(state: LeadVerification, cfg: GradingConfig) -> LeadStatus:
    """The status the tracker row and the verification stage use. Recomputed from the
    accepted queries; a stored 'complete' that the rule does not support is refused."""
    if state.status == "complete":
        rule = cfg.lead_rule
        if (
            len(state.accepted) < rule.min_distinct_queries
            or len(state.categories) < rule.min_distinct_tool_categories
        ):
            raise LeadRuleViolation("lead marked complete without the required distinct queries")
    return state.status
