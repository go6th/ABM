"""Bounded orchestration helpers for stored job observations."""

from datetime import date

from jobs.dedupe import dedupe
from jobs.errors import NotConfigured
from jobs.models import Posting, RawPosting
from jobs.ports import PostingRepo, PostingSourcePort
from jobs.window import window_start


class JobsPipeline:
    """Pull once, normalize once, and persist rows through injected ports."""

    def __init__(self, source: PostingSourcePort | None, repo: PostingRepo | None) -> None:
        self.source = source
        self.repo = repo

    async def run(self, run_id: str, lanes: list[str], as_of: date) -> list[Posting]:
        """Return existing rows on re-entry; otherwise make one bounded feed pull per lane."""
        if self.repo is None:
            raise NotConfigured("posting repository unavailable")
        existing = await self.repo.list_for_run(run_id)
        if existing:
            return existing
        if self.source is None:
            raise NotConfigured("posting source unavailable")
        raw: list[RawPosting] = []
        for lane in lanes:
            raw.extend(await self.source.pull_window(lane, window_start(as_of), as_of))
        rows = [Posting.from_raw(item, as_of) for item in dedupe(raw)]
        await self.repo.upsert(run_id, rows)
        return rows
