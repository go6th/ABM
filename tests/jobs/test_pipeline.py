"""Outage, cap, lane and re-entry behaviour of the pipeline (spec 7, 8, 9.6, 9.8)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jobs.pipeline import ROW_BOARD, ROW_PULL, ROW_READ
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder
from tests.jobs.conftest import build, frozen_config

pytestmark = pytest.mark.asyncio


async def _run(corpus: Corpus, lanes_off: frozenset[str] | None = None, **kw):  # type: ignore[no-untyped-def]
    pipeline, source, pages, first, second = build(corpus, **kw)
    rec = Recorder()
    off = corpus.lanes_off if lanes_off is None else lanes_off
    result = await run_jobs("run-x", pipeline, rec, rec, rec, rec, FakeRunInput(off, corpus.as_of))
    return result, rec, source, pages, first


async def test_stage_reentry_uses_stored_rows_without_repull(corpus: Corpus) -> None:
    pipeline, source, *_ = build(corpus)
    rec = Recorder()
    run_input = FakeRunInput(corpus.lanes_off, corpus.as_of)
    first = await run_jobs("run-1", pipeline, rec, rec, rec, rec, run_input)
    second = await run_jobs("run-1", pipeline, rec, rec, rec, rec, run_input)
    assert first.postings == second.postings
    assert sum(kind == "feed" for kind, _ in source.calls) == 1
    assert rec.saves == 1


async def test_feed_outage_blocks_the_pull_row_and_partial_counts_hold(corpus: Corpus) -> None:
    result, rec, *_ = await _run(corpus, fail_feed=True)
    rows = {e[0]: e for e in rec.evidence}
    pull = rows[ROW_PULL]
    assert len(pull[1]) == 1 and pull[4] is None  # the failed call is cited: tracker -> Blocked
    assert result.exit_reasons["pull"] == "no_progress"
    assert result.counts is not None  # board hits alone still satisfy every invariant
    assert result.counts.pulled == 1 and result.counts.distinct.read == 0
    assert rows[ROW_READ][3] is True  # nothing read: partial, never Done


async def test_partial_feed_marks_the_rows_partial(corpus: Corpus) -> None:
    result, rec, *_ = await _run(corpus, feed_complete=False, feed_exit="max_pages")
    rows = {e[0]: e for e in rec.evidence}
    assert rows[ROW_PULL][3] is True and rows[ROW_READ][3] is True
    assert result.exit_reasons["pull"] == "cap_reached"


async def test_board_keyword_failure_is_cited_not_hidden(corpus: Corpus) -> None:
    result, rec, source, *_ = await _run(corpus, fail_keywords=("SAP",))
    board = {e[0]: e for e in rec.evidence}[ROW_BOARD]
    assert len(board[1]) == 4 and board[2] == 4  # three ok plus the failed call


async def test_all_lanes_off_is_not_searched_never_zero(corpus: Corpus) -> None:
    lanes = frozenset(lane.id for lane in frozen_config().lanes)
    result, rec, source, *_ = await _run(corpus, lanes_off=lanes)
    board = {e[0]: e for e in rec.evidence}[ROW_BOARD]
    assert board[1] == () and board[4] == "lane_not_in_scope"
    assert not [c for c in source.calls if c[0] == "board"]
    assert result.counts is not None
    assert all(
        getattr(b, "reason", None) == "lane_not_in_scope"
        for b in result.counts.per_platform.values()
    )


async def test_caps_bound_escalation_and_extractor_calls(corpus: Corpus) -> None:
    cfg = frozen_config()
    tight = replace(cfg, caps={**cfg.caps, "max_page_reads": 0, "max_extractor_calls": 1})
    result, rec, source, pages, first = await _run(corpus, cfg=tight)
    assert pages.calls == []  # no read past the cap
    assert len(first.calls) == 1  # one first-pass extractor call
    assert result.exit_reasons["escalation"] == "cap_reached"
    assert result.exit_reasons["extractor"] == "cap_reached"
    by_key = {p.posting_key: p for p in result.postings}
    assert by_key["id:j8"].full_text_missing and not by_key["id:j8"].read
    assert any(p.classification and p.classification.reason == "cap" for p in result.postings)
    assert {e[0]: e for e in rec.evidence}[ROW_READ][3] is True


async def test_minimum_text_length_makes_short_text_unread(corpus: Corpus) -> None:
    cfg = frozen_config()
    strict = replace(cfg, caps={**cfg.caps, "minimum_text_length": 10_000_000})
    result, *_ = await _run(corpus, cfg=strict)
    assert result.counts is not None and result.counts.distinct.read == 0
