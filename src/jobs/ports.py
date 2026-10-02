"""Typed I/O boundaries for the job pipeline; no adapter guesses."""

from datetime import date
from typing import Protocol

from jobs.models import Posting, RawPosting


class PostingSourcePort(Protocol):
    async def search_board(self, lane: str, keyword: str) -> list[RawPosting]: ...
    async def pull_window(self, lane: str, start: date, end: date) -> list[RawPosting]: ...


class PageReadPort(Protocol):
    async def read(self, url: str, *, max_chars: int) -> RawPosting | None: ...


class PayloadStore(Protocol):
    async def get(self, run_id: str, reference: str) -> bytes | None: ...
    async def put(self, run_id: str, reference: str, payload: bytes) -> None: ...


class PostingRepo(Protocol):
    async def list_for_run(self, run_id: str) -> list[Posting]: ...
    async def upsert(self, run_id: str, postings: list[Posting]) -> None: ...


class CoverageEvidencePort(Protocol):
    async def record_evidence(self, run_id: str, row: str, evidence: dict[str, object]) -> None: ...


class GradingPort(Protocol):
    async def submit_claims(self, run_id: str, claims: list[dict[str, object]]) -> None: ...


class ExtractorPort(Protocol):
    async def extract(self, posting_id: str, text: str) -> dict[str, object]: ...


class JudgePort(Protocol):
    async def judge(self, posting_id: str, text: str) -> dict[str, object]: ...


class RunInputPort(Protocol):
    async def get_inputs(self, run_id: str) -> tuple[dict[str, object], str, date]: ...


class Clock(Protocol):
    def today(self) -> date: ...
