"""The connector-backed posting source: the feed records' last-seen and seniority reach the raw
posting, an incremental run's bound replaces the month anchor, and the active pull sends the
feed's ``active_only`` filter with no date bound."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.models import FeedArgs, JobOpeningRecord, Provenance
from jobs import adapters

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _job(job_id: str, status: str | None = None) -> JobOpeningRecord:
    return JobOpeningRecord(
        kind="job_opening",
        provenance=Provenance(
            provider_code=ProviderCode.P_SIGNALS,
            operation=Operation.JOB_OPENINGS,
            source_url=f"https://careers.acme.example/jobs/{job_id}",
            retrieved_at=T0,
            raw_ref=f"raw/{job_id}",
            page=1,
            index=0,
        ),
        lineage_hint=None,
        job_id=job_id,
        title_text="Salesforce Developer",
        normalized_title=None,
        description_text="Build Salesforce triggers.",
        url=f"https://careers.acme.example/jobs/{job_id}",
        first_seen_at="2026-09-01T00:00:00Z",
        last_seen_at="2026-09-20T12:00:00Z",
        posted_at="2026-09-01T00:00:00Z",
        status=status,
        seniority="mid_senior",
        location="Remote",
        categories=(),
    )


class _Deps:
    connectors = object()
    domain = "acme.example"
    retry_max_attempts = 3

    def call_context(self) -> Any:
        return SimpleNamespace()


async def test_feed_fields_and_the_incremental_bound_reach_the_connector(monkeypatch: Any) -> None:
    seen: list[tuple[Operation, FeedArgs]] = []

    async def fake_call(op: Operation, args: Any, ctx: Any, connectors: Any) -> Any:
        seen.append((op, args))
        return SimpleNamespace(
            records=(_job("j1"), _job("j2", "closed")),
            call_refs=("ref-1",),
            status="done",
            stop_reason="exhausted",
            counts=SimpleNamespace(read=2),
        )

    monkeypatch.setattr(adapters, "call_operation", fake_call)
    source = adapters.ConnectorPostingSource(_Deps(), "careers.acme.example")
    batch = await source.pull_window(date(2025, 10, 15), date(2026, 10, 15))
    assert seen[-1][0] is Operation.JOB_OPENINGS
    # a full pull: the bound is the first day of the window start's month
    assert seen[-1][1] == FeedArgs(domain="acme.example", since=date(2025, 10, 1))
    assert seen[-1][1].with_description_only and not seen[-1][1].active_only
    first = batch.postings[0]
    assert (first.last_seen_at, first.seniority) == ("2026-09-20T12:00:00Z", "mid_senior")
    assert first.status is None and batch.postings[1].status == "closed"

    incremental = adapters.ConnectorPostingSource(
        _Deps(), "careers.acme.example", since_override=date(2026, 10, 8)
    )
    await incremental.pull_window(date(2025, 10, 15), date(2026, 10, 15))
    assert seen[-1][1] == FeedArgs(domain="acme.example", since=date(2026, 10, 8))

    active = await incremental.pull_active()
    assert seen[-1][1] == FeedArgs(domain="acme.example", since=None, active_only=True)
    assert [p.source_id for p in active.postings] == ["j1", "j2"] and active.call_refs == ("ref-1",)


async def test_the_run_input_adapter_reads_the_previous_run_through_the_repo() -> None:
    class Repo:
        asked: list[str] = []

        async def load_previous(self, run_id: str) -> Any:
            self.asked.append(run_id)
            return "previous"

    repo = Repo()
    without = adapters.RunJobsInput(frozenset(), date(2026, 10, 1))
    assert await without.previous_postings() is None
    with_previous = adapters.RunJobsInput(
        frozenset(), date(2026, 10, 1), previous_run_id="run-1", repo=repo
    )
    assert await with_previous.previous_postings() == "previous" and repo.asked == ["run-1"]
