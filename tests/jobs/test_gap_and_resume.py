"""Gap passes over the remainder (spec 7) and the resume matrix (spec 9.7) on the synthetic corpus.

A gap pass re-processes only postings left unescalated by the page-read cap or unclassified by the
extractor cap or an isolated error. It never re-pulls, stays inside the RUN-level caps, and ends
with ``converged``, ``no_progress``, ``budget`` or ``cap_reached``.

The resume matrix kills the stage at the spec's points and re-enters it. ``LedgerSource`` and
``LedgerPages`` stand in for the connector ledger, which pays a call once per ``(run, args)`` and
replays it after that (proven against Postgres in ``tests/connectors``). So these tests prove the
jobs-side half of the bound: a resumed stage issues the same arguments and writes the same rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from jobs.errors import JobsError
from jobs.gap import gap_remainder
from jobs.models import PageText, SourceBatch
from jobs.pipeline import ROW_READ, PipelineResult, result_to_json
from jobs.ports import SourceUnavailable
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeLlm, FakeRunInput, Recorder
from tests.jobs.conftest import build, frozen_config

pytestmark = pytest.mark.asyncio


class Killed(Exception):
    """A simulated process kill (not a typed pipeline error, so nothing isolates it)."""


def _ref_for(key: object) -> str:
    """A deterministic ledger key per call arguments (the real ledger keys by args hash)."""
    return hashlib.sha256(repr(key).encode()).hexdigest()


class LedgerSource:
    """Pays each distinct source call once and replays it afterwards (the ledger's contract)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.memo: dict[tuple[str, str], SourceBatch] = {}
        self.paid = 0
        self.kill_on: str | None = None

    async def _once(self, key: tuple[str, str], call: Any) -> SourceBatch:
        if self.kill_on == key[0]:
            self.kill_on = None
            raise Killed(key[0])
        if key not in self.memo:
            self.paid += 1
            batch: SourceBatch = await call()
            ref = _ref_for(key)
            self.memo[key] = batch.model_copy(
                update={
                    "call_refs": (ref,),
                    "postings": tuple(
                        p.model_copy(update={"call_ref": ref}) for p in batch.postings
                    ),
                }
            )
        return self.memo[key]

    async def search_board(self, lane: str, keyword: str) -> SourceBatch:
        return await self._once(("board", keyword), lambda: self.inner.search_board(lane, keyword))

    async def pull_window(self, start: date, end: date, since: date | None = None) -> SourceBatch:
        return await self._once(
            ("feed", f"{since or start}"), lambda: self.inner.pull_window(start, end, since)
        )


class LedgerPages:
    """Page reads through the same ledger contract; a failed read is recorded and replayed."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.memo: dict[str, PageText | SourceUnavailable] = {}
        self.paid = 0

    async def read(self, url: str, *, max_chars: int) -> PageText:
        if url not in self.memo:
            self.paid += 1
            try:
                page = await self.inner.read(url, max_chars=max_chars)
                self.memo[url] = page.model_copy(update={"call_ref": _ref_for(url)})
            except SourceUnavailable as error:
                self.memo[url] = SourceUnavailable(error.reason, _ref_for(url))
        hit = self.memo[url]
        if isinstance(hit, SourceUnavailable):
            raise SourceUnavailable(hit.reason, hit.call_ref)
        return hit


class KillingRecorder(Recorder):
    """Recorder that can kill the process right after a save (before the hand-offs)."""

    kill_after_save: bool = False
    kill_pass: int | None = None

    async def save_run(self, run_id: str, record: Any) -> None:
        await super().save_run(run_id, record)
        if self.kill_after_save:
            self.kill_after_save = False
            raise Killed("after persist")

    async def save_pass(self, run_id: str, pass_no: int, record: Any) -> None:
        await super().save_pass(run_id, pass_no, record)
        if self.kill_pass == pass_no:
            self.kill_pass = None
            raise Killed("after gap persist")


def _flaky_once(error: type[Exception], answer: dict[str, str]) -> Any:
    """An extractor answer that raises on its first call and answers after that."""
    seen = {"n": 0}

    def call(posting_id: str, text: str, canary: str, _n: int) -> object:
        seen["n"] += 1
        if seen["n"] == 1:
            raise error("transient")
        return {"posting_id": posting_id, **answer}

    return call


J5 = {
    "label": "build",
    "evidence_sentence": (
        "This role will build and administer Tableau dashboards for the platform team."
    ),
}


def _extractor(corpus: Corpus, **override: Any) -> FakeLlm:
    answers = dict(corpus.data["extractor"])
    answers.update(override)
    return FakeLlm(answers)


def rows_hash(result: PipelineResult) -> str:
    """Hash of everything the stage persists (posting text excluded by design)."""
    return hashlib.sha256(json.dumps(result_to_json(result), sort_keys=True).encode()).hexdigest()


def _stage(corpus: Corpus, rec: Recorder, pipeline: Any) -> Any:
    return run_jobs(
        "run-g", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )


# ---- gap passes ------------------------------------------------------------------------------


async def test_clean_run_needs_no_gap_pass_and_records_converged(corpus: Corpus) -> None:
    pipeline, *_ = build(corpus)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    assert gap_remainder(result.postings) == ()
    assert result.exit_reasons["gap"] == "converged"
    assert rec.passes == {}


async def test_gap_pass_processes_only_the_errored_posting_and_never_repulls(
    corpus: Corpus,
) -> None:
    first = _extractor(corpus, **{"id:j5": _flaky_once(JobsError, J5)})
    pipeline, source, pages, _, judge = build(corpus, extractor=first)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    by_key = {p.posting_key: p for p in result.postings}
    assert by_key["id:j5"].posting_class == "build"
    assert result.pass_no == 1 and set(rec.passes) == {1}
    assert sum(kind == "feed" for kind, _ in source.calls) == 1  # no re-pull
    assert first.calls.count("id:j5") == 2 and first.calls.count("id:j6") == 1  # remainder only
    assert result.exit_reasons["gap"] == "converged"
    pass0 = rec.stored["run-g"]
    assert {p.posting_key for p in pass0.postings if p.posting_key != "id:j5"} == {
        p.posting_key for p in result.postings if p.posting_key != "id:j5"
    }
    assert result.counts is not None and result.invariant_error is None
    assert result.counts.distinct.by_class["build"] == pass0.counts.distinct.by_class["build"] + 1
    clean_pipeline, *_ = build(Corpus())
    clean = await _stage(Corpus(), Recorder(), clean_pipeline)
    assert result.counts == clean.counts  # the gap pass recovered exactly what the error lost
    assert result.claims == clean.claims


async def test_gap_pass_without_progress_ends_no_progress_after_one_pass(corpus: Corpus) -> None:
    def always(posting_id: str, text: str, canary: str, _n: int) -> object:
        raise JobsError("still failing")

    first = _extractor(corpus, **{"id:j5": always})
    pipeline, source, *_ = build(corpus, extractor=first)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    assert result.exit_reasons["gap"] == "no_progress"
    assert set(rec.passes) == {1}  # the loop stopped after the first pass without progress
    assert first.calls.count("id:j5") == 2
    assert next(e for e in result.evidence if e.row_key == ROW_READ).cap_hit


async def test_gap_pass_respects_the_run_level_caps(corpus: Corpus) -> None:
    cfg = frozen_config()
    tight = replace(cfg, caps={**cfg.caps, "max_page_reads": 0, "max_extractor_calls": 1})
    pipeline, source, pages, first, _ = build(corpus, cfg=tight)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    remainder = gap_remainder(result.postings)
    assert "id:j8" in remainder  # unescalated because of the cap
    assert pages.calls == [] and len(first.calls) == 1  # no call past the run cap in any pass
    assert result.exit_reasons["gap"] == "budget"
    assert result.page_reads_used == 0 and result.extractor_calls_used == 1


async def test_gap_passes_are_bounded_by_max_gap_passes(corpus: Corpus) -> None:
    calls = {"n": 0}

    def progress_then_fail(posting_id: str, text: str, canary: str, _n: int) -> object:
        calls["n"] += 1
        raise JobsError("transient")

    cfg = frozen_config()
    one = replace(cfg, caps={**cfg.caps, "max_gap_passes": 0})
    first = _extractor(corpus, **{"id:j5": progress_then_fail})
    pipeline, *_ = build(corpus, cfg=one, extractor=first)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    assert rec.passes == {} and calls["n"] == 1
    assert result.exit_reasons["gap"] == "cap_reached"


async def test_gap_escalation_reads_a_posting_the_cap_skipped(corpus: Corpus) -> None:
    cfg = frozen_config()
    # the corpus needs two escalation reads (j8 ok, j9 fails); a cap of one leaves one unescalated
    one_read = replace(cfg, caps={**cfg.caps, "max_page_reads": 1})
    pipeline, source, pages, *_ = build(corpus, cfg=one_read)
    rec = Recorder()
    result = await _stage(corpus, rec, pipeline)
    assert len(pages.calls) == 1  # the run-level cap holds across passes
    assert result.exit_reasons["gap"] == "budget"


async def test_gap_texts_come_from_the_payload_store(corpus: Corpus) -> None:
    first = _extractor(corpus, **{"id:j5": _flaky_once(JobsError, J5)})
    pipeline, *_ = build(corpus, extractor=first)
    rec = Recorder()
    await _stage(corpus, rec, pipeline)
    assert rec.reads and all(ref in rec.texts for ref in rec.reads)


# ---- resume matrix ---------------------------------------------------------------------------


async def _reference(corpus: Corpus, **override: Any) -> tuple[str, int]:
    pipeline, source, pages, *_ = build(corpus, extractor=_extractor(corpus, **override))
    ledger, lpages = LedgerSource(source), LedgerPages(pages)
    pipeline.source, pipeline.pages = ledger, lpages
    result = await _stage(corpus, Recorder(), pipeline)
    return rows_hash(result), ledger.paid + lpages.paid


@pytest.mark.parametrize("kill", ["board", "feed", "mid_batch", "after_persist", "after_gap"])
async def test_resume_after_a_kill_writes_the_same_rows_and_pays_nothing_twice(
    corpus: Corpus, kill: str
) -> None:
    def flaky() -> dict[str, Any]:
        return {"id:j5": _flaky_once(JobsError, J5)} if kill == "after_gap" else {}

    want_hash, want_paid = await _reference(Corpus(), **flaky())
    override = flaky()
    pipeline, source, pages, *_ = build(corpus, extractor=_extractor(corpus, **override))
    ledger, lpages = LedgerSource(source), LedgerPages(pages)
    pipeline.source, pipeline.pages = ledger, lpages
    rec = KillingRecorder()
    if kill in ("board", "feed"):
        ledger.kill_on = kill
    elif kill == "mid_batch":
        killer = {"armed": True}
        real = pipeline.extractor

        async def dying(posting_id: str, text: str, canary: str) -> object:
            if killer["armed"] and posting_id == "id:j6":
                killer["armed"] = False
                raise Killed("mid batch")
            return await real(posting_id, text, canary)

        pipeline.extractor = dying
    elif kill == "after_persist":
        rec.kill_after_save = True
    else:
        rec.kill_pass = 1
    with pytest.raises(Killed):
        await _stage(corpus, rec, pipeline)
    if kill == "after_persist":
        assert rec.evidence == [] and rec.claims == []  # the hand-offs had not happened
    result = await _stage(corpus, rec, pipeline)
    assert rows_hash(result) == want_hash
    assert ledger.paid + lpages.paid == want_paid  # no call paid twice across the kill
    assert {e[0] for e in rec.evidence} == {
        "job_board_by_lane",
        "job_postings_12_months",
        ROW_READ,
    }


async def test_replay_of_a_finished_stage_makes_no_call_of_any_kind(corpus: Corpus) -> None:
    first = _extractor(corpus, **{"id:j5": _flaky_once(JobsError, J5)})
    pipeline, source, pages, _, judge = build(corpus, extractor=first)
    rec = Recorder()
    done = await _stage(corpus, rec, pipeline)
    before = (len(source.calls), len(pages.calls), len(first.calls), len(judge.calls))
    again = await _stage(corpus, rec, pipeline)
    assert (len(source.calls), len(pages.calls), len(first.calls), len(judge.calls)) == before
    assert again.replayed and again.calls_made == 0
    assert rows_hash(again) == rows_hash(done)
