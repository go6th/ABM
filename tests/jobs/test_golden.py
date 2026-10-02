"""Whole-pipeline golden run on the synthetic corpus against the hand-written oracle (spec 9.8)."""

from __future__ import annotations

import pytest

from jobs.config import load_jobs_config
from jobs.counts import render_count_sentence
from jobs.models import CountBlock, NotSearched
from jobs.pipeline import ROW_BOARD, ROW_PULL, ROW_READ
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder, oracle
from tests.jobs.conftest import build

pytestmark = pytest.mark.asyncio


async def _run(corpus: Corpus, **kw):  # type: ignore[no-untyped-def]
    pipeline, source, pages, first, second = build(corpus, **kw)
    rec = Recorder()
    result = await run_jobs(
        "run-1", pipeline, rec, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    return result, rec, source, pages, first, second


async def test_counts_classes_sentences_and_status_equal_the_oracle(corpus: Corpus) -> None:
    want = oracle()
    result, rec, source, pages, first, second = await _run(corpus)
    counts = result.counts
    assert counts is not None and result.invariant_error is None
    chain = want["chain"]
    assert (counts.pulled, counts.deduped, counts.in_window) == (
        chain["pulled"],
        chain["deduped"],
        chain["in_window"],
    )
    assert (counts.out_of_window, counts.undated, counts.hostile) == (
        chain["out_of_window"],
        chain["undated"],
        chain["hostile"],
    )
    assert result.rejected_no_url == chain["rejected_no_url"]
    d, wd = counts.distinct, want["distinct"]
    for name in ("matched", "read", "title_only", "with_platform_sentence"):
        assert getattr(d, name) == wd[name], name
    assert (d.full_text_read, d.full_text_missing) == (
        wd["full_text_read"],
        wd["full_text_missing"],
    )
    assert d.matched_status.model_dump(exclude={"base", "total"}) == wd["matched_status"]
    assert d.read_status.model_dump(exclude={"base", "total"}) == wd["read_status"]
    assert d.by_class == {**dict.fromkeys(d.by_class, 0), **wd["by_class"]}
    for platform, expected in want["per_platform"].items():
        block = counts.per_platform[platform]
        if "not_searched" in expected:
            assert isinstance(block, NotSearched) and block.reason == expected["not_searched"]
        else:
            assert isinstance(block, CountBlock)
            assert (block.matched, block.read) == (expected["matched"], expected["read"])
    assert result.lanes_not_searched == want["lanes_not_searched"]
    by_key = {p.posting_key: p for p in result.postings}
    assert {k: by_key[k].posting_class for k in want["classes"]} == want["classes"]
    for key, sentences in want["sentences"].items():
        assert sorted([s.platform, s.text] for s in by_key[key].sentences) == sorted(sentences)
    for key, (status, src) in want["status"].items():
        assert (by_key[key].status, by_key[key].status_source) == (status, src)
    assert len(result.claims) == want["claims"] == len(rec.claims)
    assert sum(1 for kind, _ in source.calls if kind == "board") == want["board_calls"]
    assert sum(1 for kind, _ in source.calls if kind == "feed") == 1  # pulled once
    assert len(set(first.calls)) == want["extractor_first_pass"]
    assert len(pages.calls) == want["page_reads"]
    template = load_jobs_config().count_sentence
    assert render_count_sentence(d, template) == want["count_sentence"]


async def test_platform_sentences_are_verbatim_spans_of_the_stored_text(corpus: Corpus) -> None:
    result, rec, *_ = await _run(corpus)
    texts = {p.text_ref: rec.texts[p.text_ref] for p in result.postings if p.text_ref}
    import hashlib

    for posting in result.postings:
        if not posting.sentences:
            continue
        text = texts[posting.text_ref]
        for s in posting.sentences:
            span = text[s.start : s.end]
            assert hashlib.sha256(span.encode()).hexdigest() == s.sha256
            assert s.platform.casefold() in span.casefold()


async def test_row_evidence_cites_only_calls_the_sources_made(corpus: Corpus) -> None:
    result, rec, *_ = await _run(corpus)
    rows = {e[0]: e for e in rec.evidence}
    assert set(rows) == {ROW_BOARD, ROW_PULL, ROW_READ}
    assert rows[ROW_BOARD][2] == oracle()["board_calls"]  # expected_calls = keywords searched
    assert len(rows[ROW_BOARD][1]) == 4 and not rows[ROW_BOARD][3]
    assert len(rows[ROW_PULL][1]) == 1 and not rows[ROW_PULL][3]
    # matched postings remain unread (j9) or unclassified (j6, j17): the read row is partial
    assert rows[ROW_READ][3] is True


async def test_rerun_makes_zero_paid_calls_and_writes_identical_rows(corpus: Corpus) -> None:
    """J14 and spec 9.6: pull once; a replay changes nothing."""
    pipeline, source, pages, first, second = build(corpus)
    rec = Recorder()
    run_input = FakeRunInput(corpus.lanes_off, corpus.as_of)
    one = await run_jobs("run-1", pipeline, rec, rec, rec, rec, run_input)
    calls = (len(source.calls), len(pages.calls), len(first.calls), len(second.calls))
    two = await run_jobs("run-1", pipeline, rec, rec, rec, rec, run_input)
    assert (len(source.calls), len(pages.calls), len(first.calls), len(second.calls)) == calls
    assert two.replayed and two.calls_made == 0 and one.calls_made > 0
    assert two.postings == one.postings and two.counts == one.counts and rec.saves == 1


async def test_two_identical_runs_are_deterministic(corpus: Corpus) -> None:
    a, *_ = await _run(corpus)
    b, *_ = await _run(Corpus())
    strip = lambda r: [p.model_copy(update={"call_refs": ()}) for p in r.postings]  # noqa: E731
    assert strip(a) == strip(b) and a.counts == b.counts
    assert [c.model_dump() for c in a.claims] == [c.model_dump() for c in b.claims]


async def test_export_and_count_sentence_are_client_safe(corpus: Corpus) -> None:
    """J15 and spec 9.10: scanned; no provider code, ref or contact data; numbers from counts."""
    import json
    import re

    from account_intelligence.safety.client_output import ClientOutputLeak, scan_text
    from jobs.export import export_rows

    result, rec, *_ = await _run(corpus)
    texts = {p.posting_key: rec.texts[p.text_ref] for p in result.postings if p.text_ref}
    rows = export_rows(result.postings, texts)
    blob = json.dumps(rows)
    assert rows and not scan_text(blob)
    assert "p_signals" not in blob and "synthetic/raw" not in blob
    assert "jane.doe@example.com" not in blob and "555 010 9999" not in blob
    assert not any(ref in blob for e in rec.evidence for ref in e[1])
    sentence = render_count_sentence(result.counts.distinct, load_jobs_config().count_sentence)
    numbers = {int(n) for n in re.findall(r"\d+", sentence)}
    d = result.counts.distinct
    assert numbers <= {d.read, d.matched, d.read_status.open, d.read_status.closed}
    with pytest.raises(ClientOutputLeak):  # another account's name is refused on export
        export_rows(result.postings, texts, extra_terms=["ServiceNow"])
