"""Synthetic in-memory job adapters and the corpus builder (spec 9.1). No real data.

``Corpus`` reads ``tests/fixtures/synthetic/jobs/corpus.yaml`` and builds scripted fakes: a source
that counts calls and hands out ledger-style call references, a page reader, a scripted extractor
and judge (the fake LLM, keyed by posting id), and recorders for the repo, payloads, coverage and
grading hand-offs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from jobs.models import PageText, RawPosting, SourceBatch
from jobs.ports import SourceUnavailable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/synthetic/jobs"


def _ref() -> str:
    return uuid4().hex + uuid4().hex


class FakePostingSource:
    """Scripted board and feed. ``fail_feed`` makes the pull fail with a ledger reference;
    ``active`` is what the ``active_only`` pull returns (None: the feed's open postings);
    ``fail_active`` makes that pull fail."""

    def __init__(
        self,
        board: dict[str, list[RawPosting]],
        feed: list[RawPosting],
        *,
        fail_feed: bool = False,
        feed_complete: bool = True,
        feed_exit: str = "exhausted",
        fail_keywords: Sequence[str] = (),
        active: list[RawPosting] | None = None,
        fail_active: bool = False,
        active_complete: bool = True,
    ) -> None:
        self.board, self.feed = board, feed
        self.fail_feed, self.feed_complete, self.feed_exit = fail_feed, feed_complete, feed_exit
        self.fail_keywords = set(fail_keywords)
        self.active, self.fail_active = active, fail_active
        # False: the active pull stopped at a page or item ceiling (a truncated set)
        self.active_complete = active_complete
        self.calls: list[tuple[str, str]] = []
        # the delta bound each feed pull was given (None: the whole window)
        self.since_seen: list[date | None] = []

    async def search_board(self, lane: str, keyword: str) -> SourceBatch:
        self.calls.append(("board", keyword))
        ref = _ref()
        if keyword in self.fail_keywords:
            raise SourceUnavailable("source_unavailable", ref)
        hits = [p.model_copy(update={"call_ref": ref}) for p in self.board.get(keyword, [])]
        return SourceBatch(postings=tuple(hits), call_refs=(ref,), reported_count=len(hits))

    async def pull_window(self, start: date, end: date, since: date | None = None) -> SourceBatch:
        self.calls.append(("feed", f"{start}..{end}"))
        self.since_seen.append(since)
        ref = _ref()
        if self.fail_feed:
            raise SourceUnavailable("source_unavailable", ref)
        rows = tuple(p.model_copy(update={"call_ref": ref}) for p in self.feed)
        return SourceBatch(
            postings=rows,
            call_refs=(ref,),
            complete=self.feed_complete,
            exit_reason=self.feed_exit,
        )

    async def pull_active(self) -> SourceBatch:
        self.calls.append(("active", ""))
        ref = _ref()
        if self.fail_active:
            raise SourceUnavailable("source_unavailable", ref)
        rows = self.active
        if rows is None:
            rows = [p for p in self.feed if (p.status or "open").lower() in ("open", "active")]
        return SourceBatch(
            postings=tuple(p.model_copy(update={"call_ref": ref}) for p in rows),
            call_refs=(ref,),
            complete=self.active_complete,
            exit_reason="exhausted" if self.active_complete else "max_items",
        )


class FakePages:
    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def read(self, url: str, *, max_chars: int) -> PageText:
        self.calls.append(url)
        ref = _ref()
        page = self.pages.get(url)
        if page is None or not page["ok"]:
            raise SourceUnavailable("source_unavailable", ref)
        return PageText(text=page["text"][:max_chars], complete=True, call_ref=ref)


class FakeLlm:
    """Scripted, deterministic extractor or judge keyed by posting id (spec 9.4)."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def __call__(self, posting_id: str, text: str, canary: str) -> object:
        self.calls.append(posting_id)
        answer = self.answers.get(posting_id)
        if callable(answer):
            return answer(posting_id, text, canary, len(self.calls))
        if answer is None:
            return {"posting_id": posting_id, "label": "unclear", "evidence_sentence": text[:40]}
        if isinstance(answer, dict) and "label" in answer and "raw" not in answer:
            return {
                "posting_id": posting_id,
                "label": answer["label"],
                "evidence_sentence": answer["evidence"],
            }
        return answer.get("raw") if isinstance(answer, dict) else answer


@dataclass
class Recorder:
    """Repo, payload store, coverage and grading fakes in one place."""

    stored: dict[str, Any] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    evidence: list[tuple[str, tuple[str, ...], int, bool, str | None]] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    saves: int = 0
    passes: dict[int, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    async def load_run(self, run_id: str) -> Any:
        """The latest stored record: the last gap pass, else the first pass."""
        if self.passes:
            return self.passes[max(self.passes)]
        return self.stored.get(run_id)

    async def load_previous(self, run_id: str) -> Any:
        """The stored run's postings as ``PreviousPostings`` (None when never stored)."""
        from jobs.models import PreviousPostings

        record = await self.load_run(run_id)
        if record is None or record.run_id != run_id:
            return None
        return PreviousPostings(run_id=run_id, as_of=record.as_of, postings=record.postings)

    async def save_run(self, run_id: str, record: Any) -> None:
        self.saves += 1
        self.stored[run_id] = record

    async def save_pass(self, run_id: str, pass_no: int, record: Any) -> None:
        assert pass_no not in self.passes, "a gap pass is stored once"
        self.passes[pass_no] = record

    async def put_text(self, run_id: str, text: str) -> str:
        """Content-addressed like the production store (the same text gets the same ref)."""
        ref = f"synthetic/{run_id}/" + hashlib.sha256(text.encode()).hexdigest()[:16]
        self.texts[ref] = text
        return ref

    async def get_text(self, ref: str) -> str:
        self.reads.append(ref)
        return self.texts[ref]

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        self.evidence.append(
            (row_key, tuple(call_refs), expected_calls, cap_hit, not_attempted_reason)
        )

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        self.claims.extend(claims)


class FakeRunInput:
    """Lanes off, ``as_of`` and, for an incremental run, the previous research to carry."""

    def __init__(self, lanes_off: frozenset[str], as_of: date, previous: Any = None) -> None:
        self._off, self._as_of, self._previous = lanes_off, as_of, previous

    async def lanes_off(self) -> frozenset[str]:
        return self._off

    async def as_of(self) -> date:
        return self._as_of

    async def previous_postings(self) -> Any:
        return self._previous


class Corpus:
    """The synthetic corpus as postings and scripted fakes."""

    def __init__(self, path: Path = FIXTURES / "corpus.yaml") -> None:
        self.data = yaml.safe_load(path.read_text())
        assert self.data["synthetic"] is True
        self.pad = str(self.data["pad"])
        self.as_of = date.fromisoformat(self.data["as_of"])
        self.lanes_off = frozenset(self.data["lanes_off"])

    def _raw(self, item: dict[str, Any], board: bool = False) -> RawPosting:
        text = str(item["description"])
        if item.get("huge"):
            text += " filler" * 20_000
        if not board and not item.get("no_pad"):
            text += self.pad
        return RawPosting(
            source_id=item.get("id"),
            title=item["title"],
            description=text,
            url=item["url"],
            provider_code="p_signals" if not board else "p_search",
            raw_ref=f"synthetic/raw/{item.get('id', 'board')}",
            retrieved_at="2026-10-01T00:00:00+00:00",
            posted_at=item.get("posted_at"),
            first_seen_at=item.get("posted_at"),
            status=item.get("status"),
            text_complete=item.get("text_complete", True),
            last_seen_at=item.get("last_seen_at"),
            seniority=item.get("seniority"),
        )

    def source(self, **kw: Any) -> FakePostingSource:
        board = {k: [self._raw(i, True) for i in v] for k, v in self.data["board"].items()}
        return FakePostingSource(board, [self._raw(i) for i in self.data["feed"]], **kw)

    def pages(self) -> FakePages:
        pages = {
            url: {**page, "text": page.get("text", "") + self.pad}
            for url, page in self.data["pages"].items()
        }
        return FakePages(pages)

    def extractor(self) -> FakeLlm:
        return FakeLlm(dict(self.data["extractor"]))

    def judge(self) -> FakeLlm:
        return FakeLlm(dict(self.data["judge"]))


def oracle() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / "oracle.yaml").read_text())
    assert data["synthetic"] is True
    return data
