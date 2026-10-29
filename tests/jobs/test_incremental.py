"""Incremental re-research (owner request 2026-10-29, ``jobs.incremental``) on the synthetic corpus.

A second research of the same account carries every posting of the previous delivered research
with no page read and no extractor call, pulls only the delta the adapter was given, flags the new
rows, refreshes carried statuses from one ``active_only`` pull (absent: closed), keeps a posting
that fell out of the 12-month window as an out-of-window row, keeps stored statuses when the
active pull fails, and never lets a gap pass re-process a carried row.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from jobs.gap import gap_remainder, is_remainder
from jobs.incremental import (
    KEY_PREVIOUS_RUN,
    KEY_SINCE,
    OVERLAP_DAYS,
    incremental_input,
    incremental_since,
    postings_since,
    previous_run_id,
)
from jobs.models import Classification, Posting, PreviousPostings
from jobs.pipeline import ROW_PULL, PipelineResult, result_from_json, result_to_json
from jobs.stage import run_jobs
from jobs.window import window_start
from tests.fakes.jobs import Corpus, FakePostingSource, FakeRunInput, Recorder
from tests.jobs.conftest import build

pytestmark = pytest.mark.asyncio

# a posting first seen after the first research (the delta); its title cues a Build role, so it
# is the only posting of the second research that reaches the extractor
NEW = {
    "id": "j30",
    "title": "Salesforce Developer III",
    "url": "https://careers.acme.example/jobs/j30",
    "posted_at": "2026-11-20",
    "last_seen_at": "2026-11-28T00:00:00Z",
    "seniority": "mid_senior",
    "status": None,
    "description": "You will build Salesforce Apex triggers as a developer.",
}
# the overlap: a posting of the first research the delta returns again (the feed's null status)
OVERLAP = {
    "id": "j2",
    "title": "ServiceNow Administrator",
    "url": "https://careers.acme.example/jobs/j2",
    "posted_at": "2026-05-10",
    "status": None,
    "description": "The ServiceNow administrator owns the platform configuration.",
}
# open today according to the feed: j2 and j17 are NOT in it (they closed), j19 was closed anyway
UUID_RUN = UUID("00000000-0000-0000-0000-000000000001")
ACTIVE = ("j4", "j5", "j6", "j8", "j9", "j12", "j13", "j14", "j15", "j16", "j18", "j20", "j21")


async def _first(corpus: Corpus) -> tuple[PipelineResult, Recorder]:
    pipeline, *_ = build(corpus)
    rec = Recorder()
    result = await run_jobs(
        "run-1", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    return result, rec


def _delta_source(
    corpus: Corpus, delta: list[dict[str, Any]], active: tuple[str, ...], **kw: Any
) -> FakePostingSource:
    board = {k: [corpus._raw(i, True) for i in v] for k, v in corpus.data["board"].items()}
    by_id = {i["id"]: corpus._raw(i) for i in corpus.data["feed"] if "id" in i}
    return FakePostingSource(
        board, [corpus._raw(i) for i in delta], active=[by_id[i] for i in active], **kw
    )


async def _second(
    corpus: Corpus,
    first: PipelineResult,
    store: Recorder,
    *,
    delta: list[dict[str, Any]] | None = None,
    active: tuple[str, ...] = ACTIVE,
    as_of: date | None = None,
    **source_kw: Any,
) -> tuple[PipelineResult, Recorder, FakePostingSource, Any, Any]:
    pipeline, _, pages, extractor, _ = build(corpus)
    source = _delta_source(
        corpus, delta if delta is not None else [NEW, OVERLAP], active, **source_kw
    )
    pipeline.source = source
    rec = Recorder()
    previous = PreviousPostings(run_id="run-1", as_of=first.as_of, postings=first.postings)
    when = as_of or corpus.as_of + timedelta(days=60)
    result = await run_jobs(
        "run-2", pipeline, rec, store, rec, rec, FakeRunInput(corpus.lanes_off, when, previous)
    )
    return result, rec, source, pages, extractor


async def test_second_research_carries_everything_pulls_the_delta_and_flags_new_rows(
    corpus: Corpus,
) -> None:
    first, store = await _first(corpus)
    second, rec, source, pages, extractor = await _second(corpus, first, store)
    before = {p.posting_key: p for p in first.postings}
    after = {p.posting_key: p for p in second.postings}
    # every previous posting is carried, as stored: text, sentences, class, read flags, dates
    assert set(before) <= set(after)
    for key, old in before.items():
        new = after[key]
        assert new.new_since_previous is False
        for field in (
            "text_ref",
            "text_sha256",
            "sentences",
            "classification",
            "read",
            "escalation",
        ):
            assert getattr(new, field) == getattr(old, field), (key, field)
        assert new.first_seen_at == old.first_seen_at
    # the delta added one new row, flagged; the overlap row is one row, not new
    assert set(after) - set(before) == {"id:j30"}
    assert after["id:j30"].new_since_previous is True
    assert after["id:j30"].posting_class == "build" and after["id:j30"].read
    assert (
        after["id:j30"].last_seen == date(2026, 11, 28)
        and after["id:j30"].seniority == "mid_senior"
    )
    assert sum(1 for p in second.postings if p.posting_key == "id:j2") == 1
    # no paid read and no extractor call for a carried posting (the new one is the only call);
    # the feed was pulled once, the active set once
    assert pages.calls == [] and extractor.calls == ["id:j30"]
    assert [c for c in source.calls if c[0] == "feed"] == [
        ("feed", f"{window_start(second.as_of)}..{second.as_of}")
    ]
    assert [c for c in source.calls if c[0] == "active"] == [("active", "")]
    # the record names the previous run and the carried count; counts hold
    assert second.previous_run_id == "run-1" and second.carried == len(first.postings)
    assert second.counts is not None and second.invariant_error is None
    assert second.counts.deduped == len(first.postings) + 1
    assert second.counts.pulled >= second.counts.deduped
    # the carried texts came back from the payload store, nothing was stored twice
    assert {p.text_ref for p in first.postings if p.text_ref} <= set(store.reads)
    assert rec.saves == 1
    # claims are templated again for the carried postings and once for the new one
    assert len(second.claims) == len(first.claims) + 1
    # the record round-trips with the incremental fields
    again = result_from_json(result_to_json(second))
    assert again.previous_run_id == "run-1" and again.carried == second.carried
    assert again.postings == second.postings


async def test_carried_statuses_follow_the_active_set_and_the_live_board(corpus: Corpus) -> None:
    first, store = await _first(corpus)
    second, *_ = await _second(corpus, first, store)
    after = {p.posting_key: p for p in second.postings}
    # absent from the active set: closed by the feed (j2 is in the delta with a null status, the
    # active set still decides; j17 was unknown before)
    assert (after["id:j2"].status, after["id:j2"].status_source) == ("closed", "feed")
    assert (after["id:j17"].status, after["id:j17"].status_source) == ("closed", "feed")
    assert (after["id:j19"].status, after["id:j19"].status_source) == ("closed", "feed")
    # present in the active set: open by the feed
    assert (after["id:j5"].status, after["id:j5"].status_source) == ("open", "feed")
    # the live board hit this run beats everything
    assert (after["id:j1"].status, after["id:j1"].status_source) == ("open", "live_board")
    # the fresh delta posting takes the feed's status (null: open)
    assert (after["id:j30"].status, after["id:j30"].status_source) == ("open", "feed")


async def test_a_posting_that_fell_out_of_the_window_is_kept_as_a_row(corpus: Corpus) -> None:
    first, store = await _first(corpus)
    # 60 days later the window starts 2025-11-30: j11 (2025-10-01) is outside it now
    second, *_ = await _second(corpus, first, store)
    after = {p.posting_key: p for p in second.postings}
    assert {p.posting_key: p.window_bucket for p in first.postings}["id:j11"] == "in_window"
    assert after["id:j11"].window_bucket == "out_of_window"
    assert after["id:j11"].text_ref and after["id:j11"].posting_class == "build"  # nothing deleted
    assert second.counts is not None
    assert second.counts.out_of_window == first.counts.out_of_window + 1  # type: ignore[union-attr]


async def test_a_failed_active_pull_keeps_the_stored_statuses_and_cites_the_failure(
    corpus: Corpus,
) -> None:
    first, store = await _first(corpus)
    second, rec, source, *_ = await _second(corpus, first, store, delta=[NEW], fail_active=True)
    before = {p.posting_key: (p.status, p.status_source) for p in first.postings}
    after = {p.posting_key: (p.status, p.status_source) for p in second.postings}
    assert all(after[k] == v for k, v in before.items() if k != "id:j1")
    assert after["id:j1"] == ("open", "live_board")
    pull = {e[0]: e for e in rec.evidence}[ROW_PULL]
    assert len(pull[1]) == 2 and pull[2] == 2  # the feed call and the failed active call
    assert second.exit_reasons["pull"] == "converged"


async def test_a_gap_pass_never_reprocesses_a_carried_posting(corpus: Corpus) -> None:
    first, store = await _first(corpus)
    second, *_ = await _second(corpus, first, store)
    carried = {p.posting_key for p in first.postings}
    assert not set(gap_remainder(second.postings)) & carried
    capped = Posting(
        posting_key="id:x",
        title="x",
        url="https://careers.acme.example/jobs/x",
        urls=("https://careers.acme.example/jobs/x",),
        provider_code="p",
        raw_ref="r",
        retrieved_at="2026-10-01T00:00:00+00:00",
        window_bucket="in_window",
        platforms=("Salesforce",),
        read=True,
        classification=Classification(label="unclassified", classified_by="none", reason="cap"),
    )
    assert is_remainder(capped) and is_remainder(
        capped.model_copy(update={"new_since_previous": True})
    )
    assert not is_remainder(capped.model_copy(update={"new_since_previous": False}))


async def test_a_first_research_has_no_incremental_fields(corpus: Corpus) -> None:
    first, _ = await _first(corpus)
    assert first.previous_run_id is None and first.carried == 0
    assert all(p.new_since_previous is None for p in first.postings)


async def test_a_replay_of_the_second_research_makes_no_call(corpus: Corpus) -> None:
    first, store = await _first(corpus)
    second, rec, source, pages, extractor = await _second(corpus, first, store)
    calls = (len(source.calls), len(pages.calls), len(extractor.calls))
    pipeline, *_ = build(corpus)
    pipeline.source = source
    previous = PreviousPostings(run_id="run-1", as_of=first.as_of, postings=first.postings)
    again = await run_jobs(
        "run-2",
        pipeline,
        rec,
        store,
        rec,
        rec,
        FakeRunInput(corpus.lanes_off, second.as_of, previous),
    )
    assert again.replayed and again.calls_made == 0
    assert (len(source.calls), len(pages.calls), len(extractor.calls)) == calls
    assert again.postings == second.postings


async def test_the_delta_bound_is_the_previous_date_minus_the_overlap_inside_the_window() -> None:
    as_of = date(2026, 11, 30)
    assert incremental_since(date(2026, 11, 23), as_of) == date(2026, 11, 23) - timedelta(
        days=OVERLAP_DAYS
    )
    assert incremental_since(as_of, as_of) == as_of - timedelta(days=OVERLAP_DAYS)
    # a previous research older than the window: a full pull is the delta anyway
    assert incremental_since(date(2025, 11, 1), as_of) is None
    # never before the window start
    assert incremental_since(date(2025, 12, 2), as_of) == window_start(as_of)
    assert incremental_since(date(2027, 1, 1), as_of) is None  # a later date: nothing to carry
    keys = incremental_input((UUID_RUN, date(2026, 11, 23)), as_of)
    assert keys == {KEY_PREVIOUS_RUN: str(UUID_RUN), KEY_SINCE: "2026-11-16"}
    assert incremental_input(None, as_of) == {}
    assert incremental_input((UUID_RUN, date(2025, 1, 1)), as_of) == {}
    assert postings_since(keys) == date(2026, 11, 16) and previous_run_id(keys) == str(UUID_RUN)
    assert postings_since({}) is None and postings_since(None) is None
    assert postings_since({KEY_SINCE: "not a date"}) is None
    assert previous_run_id({KEY_PREVIOUS_RUN: "not a uuid"}) is None
