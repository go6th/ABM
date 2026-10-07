"""Typed I/O boundaries of the people pipeline. A production adapter that does not exist raises
``NotConfigured`` and the affected rows end Not done with ``not_available_yet``; nothing guesses.

Production adapters: ``people.adapters``. Fakes: ``tests/fakes/people.py``. The pipeline asks for
a need (title search, association search, profile, posts); the router behind the connector layer
picks the provider, single or dual people-source mode (P0-3). No operation that returns contact
details is part of any port (P14).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from jobs.ports import SourceUnavailable
from people.models import Mention, PersonPage, Post, PostsBatch, Profile, RawProfile

__all__ = [
    "BUDGET_REASONS",
    "CostPort",
    "CoverageEvidencePort",
    "ExtractorPort",
    "GradingPort",
    "MentionResolverPort",
    "MentionsPort",
    "PeopleRepo",
    "PeopleSearchPort",
    "PostsPort",
    "ProfilePort",
    "RunInputPort",
    "SourceRegistryPort",
    "SourceUnavailable",
    "StoredPage",
]

# Reasons a port raises when the run's money or time is spent; loops stop rather than continue.
BUDGET_REASONS = frozenset({"budget_cap", "deadline"})


class PeopleSearchPort(Protocol):
    """People search on the account domain. Each call returns one page; ``cursor`` continues the
    same source (never re-adds it)."""

    async def search_titles(
        self, lane: str, pass_no: int, titles: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        """One page of the title search for (lane, pass)."""
        ...

    async def search_association(
        self, lane: str, functions: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        """One page of the association search (gap filling) for a lane."""
        ...


class MentionsPort(Protocol):
    """People named in research by earlier stages (stored facts and sources)."""

    async def mentions(self) -> tuple[Mention, ...]:
        """The mentions of this run, each with its URL and sentence."""
        ...


class MentionResolverPort(Protocol):
    """One bounded query to find the profile URL of a named person."""

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        """Candidates for ``name``; ``attempt`` numbers the differently worded query."""
        ...


class ProfilePort(Protocol):
    """One full-profile pull by profile URL (allow-listed fields only)."""

    async def pull(self, profile_url: str) -> RawProfile:
        """The profile behind ``profile_url``."""
        ...


class PostsPort(Protocol):
    """Posts and comments of one person since a date."""

    async def pull(self, profile_url: str, since: date) -> PostsBatch:
        """Posts of the person behind ``profile_url``."""
        ...


class CostPort(Protocol):
    """The recorded cost of a call from the ledger; ``None`` when unknown (never 0)."""

    async def cost_usd(self, call_ref: str) -> float | None:
        """Recorded USD cost of the call."""
        ...


class StoredPage(Protocol):
    """A search page already stored for this run (resume)."""

    source_id: str
    page_no: int
    cursor_in: str | None
    page: PersonPage


class PeopleRepo(Protocol):
    """Persistence through ``tenant_txn``; every write is append-only except the people rows."""

    async def load_run(self, run_id: str) -> Any:
        """The stored pipeline record of the run, or None."""
        ...

    async def load_pages(self, run_id: str) -> Sequence[Any]:
        """Search pages stored for the run, in (source, page) order."""
        ...

    async def save_page(
        self, run_id: str, source_id: str, page_no: int, cursor_in: str | None, page: PersonPage
    ) -> None:
        """Store one search page (idempotent on run, source and page)."""
        ...

    async def stored_profiles(self, profile_urls: Sequence[str]) -> dict[str, Profile]:
        """Profiles already pulled for this account, by canonical URL."""
        ...

    async def save_profiles(self, run_id: str, profiles: Sequence[Profile]) -> None:
        """Store a batch of profiles (append-only)."""
        ...

    async def stored_posts(self, run_id: str) -> dict[str, tuple[Post, ...]]:
        """Posts already pulled in this run, by canonical profile URL."""
        ...

    async def save_posts(self, run_id: str, profile_url: str, posts: Sequence[Post]) -> None:
        """Store one person's posts (append-only; an empty list records the pull)."""
        ...

    async def save_run(self, run_id: str, record: Any) -> None:
        """Persist the people rows, events and the run-level record."""
        ...


class SourceRegistryPort(Protocol):
    """Registers a URL as a source of the account; returns its stable code (S-0001...)."""

    async def code_for(self, url: str) -> str:
        """The stable per-account source code of ``url``."""
        ...


class CoverageEvidencePort(Protocol):
    """Hands row evidence to the tracker (``record_outcome``); never writes a status."""

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        """Hand one row's evidence to the tracker."""
        ...


class GradingPort(Protocol):
    """Submits templated claims and Findings to ``grade_claims``; never sets a grade (P15)."""

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        """Hand claims to grading."""
        ...


class ExtractorPort(Protocol):
    """The tool-less extractor: ``(person_key, text, canary) -> raw output`` (P13)."""

    async def __call__(self, person_key: str, text: str, canary: str) -> object: ...


class RunInputPort(Protocol):
    """Scope, lanes, reference employers, budget and ``as_of`` of the run."""

    async def lanes_off(self) -> frozenset[str]:
        """Lanes switched off for the run."""
        ...

    async def as_of(self) -> date:
        """The run's as_of date."""
        ...

    async def regions(self) -> tuple[str, ...]:
        """Regions the scope is narrowed to (empty = global)."""
        ...

    async def reference_employers(self) -> tuple[str, ...] | None:
        """The pursuing client and employers the operator named; ``None`` when not given."""
        ...

    async def people_budget_usd(self) -> float | None:
        """Budget left for this stage in USD; ``None`` when unknown."""
        ...

    async def excluded_people(self) -> tuple[str, ...]:
        """People the tenant excludes, in ``tenancy.settings.normalise_person`` form (TN5)."""
        ...
