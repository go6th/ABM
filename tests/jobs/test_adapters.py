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
    # the description filter is not sent: a posting without one can still be page-read
    assert not seen[-1][1].with_description_only and not seen[-1][1].active_only
    first = batch.postings[0]
    assert (first.last_seen_at, first.seniority) == ("2026-09-20T12:00:00Z", "mid_senior")
    assert first.status is None and batch.postings[1].status == "closed"

    incremental = adapters.ConnectorPostingSource(_Deps(), "careers.acme.example")
    await incremental.pull_window(date(2025, 10, 15), date(2026, 10, 15), date(2026, 10, 8))
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


async def test_a_lookalike_seen_by_the_window_pull_and_the_active_pull_is_counted_once(
    monkeypatch: Any,
) -> None:
    """Review 2026-10-29, finding 27: the same look-alike record comes back in the delta and in
    the active pull; each call counts its own drops, the source total counts the pull once."""
    from account_intelligence.connectors.models import FeedArgs as _FeedArgs  # noqa: F401

    def foreign(job_id: str) -> JobOpeningRecord:
        record = _job(job_id)
        return record.model_copy(update={"url": f"https://jobs.lookalike.example/{job_id}"})

    async def fake_call(op: Operation, args: Any, ctx: Any, connectors: Any) -> Any:
        return SimpleNamespace(
            records=(_job("j1"), foreign("j2")),
            call_refs=("ref-1",),
            status="done",
            stop_reason="exhausted",
            counts=SimpleNamespace(read=2),
        )

    monkeypatch.setattr(adapters, "call_operation", fake_call)
    source = adapters.ConnectorPostingSource(_Deps(), "careers.acme.example", ["lookalike.example"])
    delta = await source.pull_window(date(2025, 10, 15), date(2026, 10, 15), date(2026, 10, 8))
    active = await source.pull_active()
    assert [p.source_id for p in delta.postings] == ["j1"] and delta.dropped == 1
    assert [p.source_id for p in active.postings] == ["j1"] and active.dropped == 1
    assert source.dropped == 1  # not 2: the active pull sees the same record again


async def test_page_reads_wait_at_the_research_own_gate_not_at_the_governor(
    monkeypatch: Any,
) -> None:
    """Many postings are read at once; the scrape provider's slots (config/parallel.yaml) hold, and
    the wait for a gate is not counted as a slow source."""
    import asyncio

    from parallel.config import load_parallel_config

    ceiling = load_parallel_config().providers.ceiling("p_scrape")
    flight = {"now": 0, "peak": 0}

    async def fake(op: Operation, args: Any, ctx: Any, runtime: Any) -> Any:
        flight["now"] += 1
        flight["peak"] = max(flight["peak"], flight["now"])
        await asyncio.sleep(0.01)
        flight["now"] -= 1
        return SimpleNamespace(records=(), call_refs=("r",))

    monkeypatch.setattr(adapters, "call_operation", fake)
    reader = adapters.ConnectorPageReader(_Deps())
    await asyncio.gather(
        *(reader.read(f"https://careers.acme.example/j/{i}", max_chars=100) for i in range(30))
    )
    assert 1 < flight["peak"] <= ceiling


async def test_the_posting_texts_are_written_side_by_side_and_keep_their_keys() -> None:
    import asyncio
    import time
    from dataclasses import dataclass

    from jobs import stage

    class Store:
        async def put_text(self, run_id: str, text: str) -> str:
            await asyncio.sleep(0.02)
            return f"ref/{text}"

    def posting(key: str) -> Any:
        return SimpleNamespace(
            posting_key=key,
            text_ref=None,
            model_copy=lambda update, key=key: SimpleNamespace(posting_key=key, **update),
        )

    @dataclass(frozen=True)
    class Result:
        postings: tuple[Any, ...]

    postings = tuple(posting(f"k{i}") for i in range(24))
    # k3 has an empty text: nothing is written for it and it keeps no reference
    pipeline = SimpleNamespace(last_texts={f"k{i}": f"t{i}" for i in range(24)} | {"k3": ""})
    started = time.monotonic()
    out = await stage._store_texts("run", Result(postings), pipeline, Store())  # type: ignore[arg-type]
    assert time.monotonic() - started < 0.2  # 23 writes of 0.02 s one at a time: 0.46 s
    assert [p.posting_key for p in out.postings] == [f"k{i}" for i in range(24)]
    assert out.postings[0].text_ref == "ref/t0" and out.postings[3].text_ref is None
