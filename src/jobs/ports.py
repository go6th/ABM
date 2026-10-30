"""Typed I/O boundaries for the job pipeline. Adapters never guess: a missing production adapter
raises ``NotConfigured`` and the affected rows end Not done with ``not_available_yet``.

Production adapters: ``jobs.adapters`` (connector-backed source and page reads, Postgres repo,
tracker evidence, grading). Fakes: ``tests/fakes/jobs.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from jobs.models import PageText, SourceBatch


class SourceUnavailable(Exception):
    """A source call failed (typed connector error). ``call_ref`` is the failed attempt's ledger
    key, cited as tracker proof of the failure (T6)."""

    def __init__(self, reason: str, call_ref: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.call_ref = call_ref


class PostingSourcePort(Protocol):
    """Live board search (one call per lane keyword) and the 12-month feed pull (pulled once)."""

    async def search_board(self, lane: str, keyword: str) -> SourceBatch:
        """One live board search per lane keyword."""
        ...

    async def pull_window(self, start: date, end: date, since: date | None = None) -> SourceBatch:
        """The 12-month pull of open and closed postings. ``since`` is an incremental run's delta
        bound, given only when the previous research's record was loaded (None: the whole
        window)."""
        ...

    async def pull_active(self) -> SourceBatch:
        """The postings the feed lists as open today (no date bound): a carried posting absent
        from this set has closed since the previous research."""
        ...


class PageReadPort(Protocol):
    """One bounded read of a posting URL as returned by a source (never a constructed URL)."""

    async def read(self, url: str, *, max_chars: int) -> PageText:
        """One read of a posting URL."""
        ...


class PayloadStore(Protocol):
    """Stores posting text under a neutral key; returns the reference."""

    async def put_text(self, run_id: str, text: str) -> str:
        """Store posting text; returns its reference."""
        ...

    async def get_text(self, ref: str) -> str:
        """The stored text behind a reference (a gap pass re-reads it; no paid call)."""
        ...


class PostingRepo(Protocol):
    """Persistence for one run's postings and its pipeline record (append-only side tables)."""

    async def load_run(self, run_id: str) -> Any:
        """The latest stored pipeline record of the run (last gap pass, else first), or None."""
        ...

    async def load_previous(self, run_id: str) -> Any:
        """The stored postings of an earlier run OF THIS ACCOUNT as ``PreviousPostings``, or None
        (another account's or tenant's run is never returned)."""
        ...

    async def save_run(self, run_id: str, record: Any) -> None:
        """Persist the first pass's record and its rows."""
        ...

    async def save_pass(self, run_id: str, pass_no: int, record: Any) -> None:
        """Persist a gap pass (append-only: new classifications and sentences, the pass record)."""
        ...


class CoverageEvidencePort(Protocol):
    """Hands row evidence to the tracker (``record_outcome``); never writes a status (spec 0.3)."""

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        """Hand row evidence to the tracker."""
        ...


class GradingPort(Protocol):
    """Submits templated claims to ``grade_claims``; the pipeline never sets a grade (J11)."""

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        """Hand templated claims to grading."""
        ...


class ExtractorPort(Protocol):
    """The tool-less extractor (first pass): ``(posting_id, text, canary) -> raw output``."""

    async def __call__(self, posting_id: str, text: str, canary: str) -> object: ...


class RunInputPort(Protocol):
    """Lanes switched off for this run, the verified careers host and the ``as_of`` date."""

    async def lanes_off(self) -> frozenset[str]:
        """Lanes switched off for the run."""
        ...

    async def as_of(self) -> date:
        """The run's as_of date."""
        ...

    async def previous_postings(self) -> Any:
        """The previous delivered research to carry forward (``jobs.models.PreviousPostings``),
        or None for a first research."""
        ...
