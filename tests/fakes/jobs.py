"""Synthetic in-memory job adapters for deterministic pipeline checks."""

from datetime import date

from jobs.models import Posting, RawPosting


class FakePostingSource:
    def __init__(self, rows: list[RawPosting]) -> None:
        self.rows = rows
        self.calls = 0

    async def search_board(self, lane: str, keyword: str) -> list[RawPosting]:
        self.calls += 1
        return []

    async def pull_window(self, lane: str, start: date, end: date) -> list[RawPosting]:
        self.calls += 1
        return list(self.rows)


class FakePostingRepo:
    def __init__(self) -> None:
        self.rows: dict[str, list[Posting]] = {}
        self.writes = 0

    async def list_for_run(self, run_id: str) -> list[Posting]:
        return self.rows.get(run_id, [])

    async def upsert(self, run_id: str, postings: list[Posting]) -> None:
        self.writes += 1
        self.rows[run_id] = list(postings)
