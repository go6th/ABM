"""G5: "read in a primary source this run", computed by code only. Pure.

True only when some evidence item satisfies all of: its supporting sentence was verified in
the text of a specific fetch (G4); that fetch is a successful **full page read** (not a
search snippet, feed item, broker record or cached copy); the source tier is T1; the fetch
belongs to this run and happened at or after run start; the fetched URL is the cited URL;
and the attribute is not Conflicting. No caller parameter can set it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from account_intelligence.grading.ports import FetchRecord, RunInfo


@dataclass(frozen=True)
class VerifiedEvidence:
    """Evidence whose sentence was verified in a stored source (G4 passed)."""

    url_normalized: str
    tier: str
    call_ref: str  # the fetch whose stored text contains the verified sentence
    fetch_url_normalized: str
    span_verified: bool


@dataclass(frozen=True)
class PrimaryRead:
    """Whether a claim was read in a primary source this run, and why not."""

    value: bool
    reasons: tuple[str, ...]  # why each evidence item did not qualify (empty when true)


def _why_not(e: VerifiedEvidence, fetches: Mapping[str, FetchRecord], run: RunInfo) -> str | None:
    if not e.span_verified:
        return "span_not_verified"
    if e.tier != "T1":
        return "not_t1"
    fetch = fetches.get(e.call_ref)
    if fetch is None:
        return "fetch_not_in_ledger"
    if not fetch.success:
        return "fetch_failed"
    if fetch.origin != "full_page_read":
        return f"origin_{fetch.origin}"
    if fetch.run_id != run.run_id:
        return "fetch_from_other_run"
    if fetch.fetched_at < run.started_at:
        return "fetched_before_run_start"
    if e.fetch_url_normalized != e.url_normalized:
        return "fetched_url_differs"
    return None


def read_in_primary_this_run(
    evidence: Sequence[VerifiedEvidence],
    fetches: Mapping[str, FetchRecord],
    run: RunInfo,
    *,
    conflicting: bool,
) -> PrimaryRead:
    """Compute the primary-read flag from the fetch ledger (G5; no caller can set it)."""
    if conflicting:
        return PrimaryRead(False, ("conflicting",))
    reasons: list[str] = []
    for item in evidence:
        why = _why_not(item, fetches, run)
        if why is None:
            return PrimaryRead(True, ())
        reasons.append(why)
    return PrimaryRead(False, tuple(sorted(set(reasons))) or ("no_evidence",))
