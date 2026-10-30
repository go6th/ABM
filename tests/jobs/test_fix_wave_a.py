"""Pipeline review of 2026-10-29, fix wave A (pure parts, synthetic data only).

One test per finding it pins; each fails on the code the review read:

* 1  a cut-short active pull closes nothing; a posting only the live board holds, and one stored
     without a description, are never closed by absence from the feed's open set;
* 2  the delta bound is applied only when the previous research's record was loaded;
* 8  two fresh records that resolve to one stored posting give one posting, not an invariant error;
* 9  the new feed filters leave the ledger key of an unchanged request alone; the postings that
     came without a description are counted in the pass record;
* 14 a posting only the live board holds keeps the day it was first captured;
* 15 a gap pass reads only the remainder's stored texts and survives a missing object;
* 27 (see test_adapters).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from account_intelligence.connectors.ledger import args_hash
from account_intelligence.connectors.models import FeedArgs
from jobs.errors import JobsError
from jobs.models import Posting, PreviousPostings, RawPosting
from jobs.pipeline import ROW_PULL, result_from_json, result_to_json
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakePostingSource, FakeRunInput, Recorder
from tests.jobs.conftest import build
from tests.jobs.test_gap_and_resume import J5, _extractor, _flaky_once
from tests.jobs.test_incremental import NEW, _first, _second

pytestmark = pytest.mark.asyncio

AS_OF = date(2026, 10, 1)
T1 = "2026-10-01T00:00:00+00:00"


def _stored(key: str, **kw: Any) -> Posting:
    url = f"https://careers.acme.example/jobs/{key.split(':')[-1]}"
    base: dict[str, Any] = {
        "posting_key": key,
        "title": "Salesforce Developer",
        "url": url,
        "urls": (url,),
        "provider_code": "p",
        "raw_ref": "raw/" + key,
        "retrieved_at": T1,
        "window_bucket": "in_window",
        "status": "open",
        "status_source": "feed",
        "text_sha256": "a" * 64,
    }
    return Posting(**{**base, **kw})


def _raw(posting_id: str | None, url: str, description: str | None = "x", **kw: Any) -> RawPosting:
    base: dict[str, Any] = {
        "source_id": posting_id,
        "title": "Salesforce Developer",
        "description": description,
        "url": url,
        "provider_code": "p",
        "raw_ref": "raw/" + (posting_id or url),
        "retrieved_at": T1,
        "posted_at": "2026-09-01",
    }
    return RawPosting(**{**base, **kw})


async def _carry_run(
    previous: tuple[Posting, ...], source: FakePostingSource, *, as_of: date = AS_OF
) -> Any:
    corpus = Corpus()
    pipeline, *_ = build(corpus)
    pipeline.source = source
    rec = Recorder()
    prior = PreviousPostings(run_id="run-1", as_of=date(2026, 9, 1), postings=previous)
    result = await run_jobs(
        "run-2", pipeline, rec, rec, rec, rec, FakeRunInput(frozenset(), as_of, prior)
    )
    return result, rec


# ---- finding 1 -------------------------------------------------------------------------------


async def test_a_cut_short_active_pull_closes_nothing(corpus: Corpus) -> None:
    first, store = await _first(corpus)
    second, rec, *_ = await _second(corpus, first, store, delta=[NEW], active_complete=False)
    before = {p.posting_key: (p.status, p.status_source) for p in first.postings}
    after = {p.posting_key: (p.status, p.status_source) for p in second.postings}
    # the default active set leaves several open postings out; cut short, it proves none closed
    assert all(after[k] == v for k, v in before.items() if k != "id:j1")
    pull = {e[0]: e for e in rec.evidence}[ROW_PULL]
    assert pull[3] is True  # the row says the pull was cut short
    assert len(pull[1]) == 2 and pull[2] == 2  # the feed call and the active call are cited


async def test_absence_does_not_close_a_board_only_or_a_description_less_posting() -> None:
    board_only = _stored("url:https://careers.acme.example/jobs/b1", status_source="live_board")
    no_text = _stored("id:d1", text_sha256=None)
    plain = _stored("id:c1")
    result, _ = await _carry_run((board_only, no_text, plain), FakePostingSource({}, [], active=[]))
    status = {p.posting_key: (p.status, p.status_source) for p in result.postings}
    assert status[board_only.posting_key] == ("open", "live_board")
    assert status["id:d1"] == ("open", "feed")
    assert status["id:c1"] == ("closed", "feed")  # a posting with a feed identity does close


# ---- finding 2 -------------------------------------------------------------------------------


async def test_the_delta_bound_is_applied_only_when_the_previous_record_was_loaded(
    corpus: Corpus,
) -> None:
    pipeline, source, *_ = build(corpus)
    pipeline.delta_since = date(2026, 9, 20)
    rec = Recorder()
    await run_jobs(
        "run-1", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    assert source.since_seen == [None]  # no previous record: the whole window
    first, store = await _first(Corpus())
    pipeline, source, *_ = build(Corpus())
    pipeline.delta_since = date(2026, 9, 20)
    prior = PreviousPostings(run_id="run-1", as_of=first.as_of, postings=first.postings)
    await run_jobs(
        "run-2",
        pipeline,
        Recorder(),
        store,
        Recorder(),
        Recorder(),
        FakeRunInput(corpus.lanes_off, corpus.as_of, prior),
    )
    assert source.since_seen == [date(2026, 9, 20)]


# ---- finding 8 -------------------------------------------------------------------------------


async def test_two_fresh_records_that_resolve_to_one_stored_posting_give_one_posting() -> None:
    u1, u2 = "https://careers.acme.example/jobs/s1", "https://jobs.acme.example/s1-repost"
    stored = _stored("id:s1", url=u1, urls=(u1, u2))
    feed = [_raw("n1", u1), _raw("n2", u2)]
    result, _ = await _carry_run((stored,), FakePostingSource({}, feed, active=feed))
    assert [p.posting_key for p in result.postings] == ["id:s1"]
    assert result.invariant_error is None and result.counts is not None
    assert set(result.postings[0].urls) == {u1, u2}


# ---- finding 9 -------------------------------------------------------------------------------


async def test_the_new_feed_filters_leave_the_ledger_key_of_an_unchanged_request_alone() -> None:
    args = FeedArgs(domain="acme.example", since=date(2026, 9, 1))
    old_shape = {"domain": "acme.example", "since": "2026-09-01"}  # before the filters existed
    assert args.model_dump(mode="json") == old_shape
    assert args_hash(
        "p_signals", "news_events", {"args": args.model_dump(mode="json"), "cursor": None}
    ) == args_hash("p_signals", "news_events", {"args": old_shape, "cursor": None})
    active = FeedArgs(domain="acme.example", active_only=True)
    assert active.model_dump(mode="json")["active_only"] is True  # a real filter is a new key
    assert FeedArgs.model_validate(active.model_dump()) == active
    assert FeedArgs(domain="acme.example").with_description_only is False  # not sent by default


async def test_postings_that_came_without_a_description_are_counted_in_the_pass_record() -> None:
    feed = [
        _raw("a1", "https://careers.acme.example/jobs/a1", None),
        _raw("a2", "https://careers.acme.example/jobs/a2", "   "),
        _raw("a3", "https://careers.acme.example/jobs/a3", "Build Salesforce triggers."),
    ]
    corpus = Corpus()
    pipeline, *_ = build(corpus)
    pipeline.source = FakePostingSource({}, feed)
    rec = Recorder()
    result = await run_jobs("run-d", pipeline, rec, rec, rec, rec, FakeRunInput(frozenset(), AS_OF))
    assert result.description_less == 2
    again = result_from_json(result_to_json(result))
    assert again.description_less == 2 and again.pull_complete and again.lexicon_hash
    assert again.lanes_off == ()


# ---- finding 14 ------------------------------------------------------------------------------


async def test_a_board_only_posting_keeps_the_day_it_was_first_captured() -> None:
    url = "https://careers.acme.example/jobs/live-only"
    board = {
        "Salesforce": [
            _raw(None, url, "Build Salesforce triggers.", live_board_present=True, posted_at=None)
        ]
    }
    corpus = Corpus()
    pipeline, *_ = build(corpus)
    pipeline.source = FakePostingSource(board, [])
    rec = Recorder()
    first = await run_jobs("run-1", pipeline, rec, rec, rec, rec, FakeRunInput(frozenset(), AS_OF))
    key = f"url:{url}"
    held = {p.posting_key: p for p in first.postings}[key]
    assert held.captured_on == date(2026, 10, 1) and held.first_seen_at is None
    # a later research sees it again on the board, retrieved on a later day
    later = {
        "Salesforce": [
            _raw(
                None,
                url,
                "Build Salesforce triggers.",
                live_board_present=True,
                posted_at=None,
                retrieved_at="2026-12-01T00:00:00+00:00",
            )
        ]
    }
    pipeline2, *_ = build(corpus)
    pipeline2.source = FakePostingSource(later, [])
    prior = PreviousPostings(run_id="run-1", as_of=first.as_of, postings=first.postings)
    second = await run_jobs(
        "run-2",
        pipeline2,
        Recorder(),
        rec,
        Recorder(),
        Recorder(),
        FakeRunInput(frozenset(), date(2026, 12, 1), prior),
    )
    carried = {p.posting_key: p for p in second.postings}[key]
    assert carried.captured_on == date(2026, 10, 1)  # unchanged by the later sighting
    assert carried.retrieved_at.startswith("2026-12-01")


# ---- finding 15 ------------------------------------------------------------------------------


async def test_a_gap_pass_reads_only_the_remainders_texts(corpus: Corpus) -> None:
    extractor = _extractor(corpus, **{"id:j5": _flaky_once(JobsError, J5)})
    pipeline, *_ = build(corpus, extractor=extractor)
    rec = Recorder()
    result = await run_jobs(
        "run-g", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    j5 = {p.posting_key: p for p in result.postings}["id:j5"]
    assert result.pass_no == 1 and result.exit_reasons["gap"] == "converged"
    assert rec.reads == [j5.text_ref]  # one stored text read, not one per posting


async def test_a_missing_stored_text_does_not_fail_the_gap_pass(corpus: Corpus) -> None:
    class Missing(Recorder):
        async def get_text(self, ref: str) -> str:
            raise KeyError(ref)  # the object is gone

    extractor = _extractor(corpus, **{"id:j5": _flaky_once(JobsError, J5)})
    pipeline, *_ = build(corpus, extractor=extractor)
    rec = Missing()
    result = await run_jobs(
        "run-g", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    assert result.pass_no == 1 and result.invariant_error is None


# ---- findings 3 and 4 (pure parts) -----------------------------------------------------------


async def test_the_claim_id_ties_a_claim_to_its_posting_and_platform() -> None:
    from jobs.claims import claim_id, claims_for
    from jobs.models import Classification, PlatformSentence

    text = "You will build Salesforce triggers as a developer. More text."
    sentence = PlatformSentence(
        platform="Salesforce",
        text="You will build Salesforce triggers as a developer.",
        start=0,
        end=49,
        sha256="a" * 64,
    )
    posting = _stored(
        "id:c9",
        read=True,
        platforms=("Salesforce",),
        sentences=(sentence,),
        classification=Classification(label="build", classified_by="extractor", reason="x"),
    )
    made, _ = claims_for(posting, text)
    assert [c.local_id for c in made] == [claim_id("id:c9", "Salesforce")]


async def test_a_posting_past_the_window_is_not_exported() -> None:
    from jobs.export import is_exported

    read = _stored("id:w1", read=True, platforms=("Salesforce",))
    assert is_exported(read)
    assert not is_exported(read.model_copy(update={"window_bucket": "out_of_window"}))
    assert is_exported(read.model_copy(update={"window_bucket": "undated"}))
