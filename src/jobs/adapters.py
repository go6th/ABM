"""Production adapters for the job pipeline ports, over the connector layer and the run.

* ``ConnectorPostingSource.search_board``: one ``web_search`` per lane keyword restricted to the
  VERIFIED careers host (never a constructed URL, J13). The connector layer has no keyword
  job-board operation; open item OI-JOBS-2. Hits are live-board observations with excerpt text.
* ``ConnectorPostingSource.pull_window``: the ``job_openings`` feed, pulled once (the ledger
  replays a second request). Description completeness is not stated by the sourced provider
  contract, so ``text_complete`` is ``None`` (unknown counts as not read, J2) and matched
  postings are escalated to a page read (bounded).
* ``ConnectorPageReader``: ``scrape_job_page`` in plain mode on the posting URL as returned.
* Retry ownership (W6): a retryable error below the node attempt budget is re-raised.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import ConnectorError, RetryableProviderError
from account_intelligence.connectors.models import (
    FeedArgs,
    JobOpeningRecord,
    ScrapedPageRecord,
    ScrapeJobPageArgs,
    WebPageRecord,
    WebSearchArgs,
)
from account_intelligence.connectors.service import call_operation
from account_intelligence.tracker.model import Actor, ReasonCode
from jobs.models import PageText, RawPosting, SourceBatch
from jobs.ports import SourceUnavailable


class _Calls:
    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def call(self, op: Operation, args: Any) -> Any:
        """One connector call with the retry-ownership rule (re-raise below the node budget)."""
        if self.deps.connectors is None:
            raise SourceUnavailable("not_available_yet")
        try:
            return await call_operation(op, args, self.deps.call_context(), self.deps.connectors)
        except RetryableProviderError as error:
            if (error.attempt or 1) < self.deps.retry_max_attempts:
                raise
            raise SourceUnavailable("source_unavailable", error.call_ref) from error
        except ConnectorError as error:
            raise SourceUnavailable("source_unavailable", error.call_ref) from error


class ConnectorPostingSource(_Calls):
    """``PostingSourcePort`` over ``call_operation``."""

    def __init__(self, deps: Any, careers_host: str | None) -> None:
        super().__init__(deps)
        self.careers_host = careers_host

    async def search_board(self, lane: str, keyword: str) -> SourceBatch:
        """One live board search for a lane keyword on the verified careers host."""
        if not self.careers_host:
            raise SourceUnavailable("careers_host_not_verified")
        result = await self.call(
            Operation.WEB_SEARCH,
            WebSearchArgs(queries=(f"{keyword} jobs",), include_domains=(self.careers_host,)),
        )
        hits = []
        for record in result.records:
            if not isinstance(record, WebPageRecord):
                continue
            hits.append(
                RawPosting(
                    title=record.title_text or record.url,
                    description="\n".join(record.excerpts_text) or None,
                    url=record.url,
                    provider_code=record.provenance.provider_code.value,
                    raw_ref=record.provenance.raw_ref,
                    retrieved_at=record.provenance.retrieved_at.isoformat(),
                    text_complete=False,
                    live_board_present=True,
                    call_ref=result.call_refs[0] if result.call_refs else None,
                )
            )
        return SourceBatch(
            postings=tuple(hits),
            call_refs=tuple(result.call_refs),
            complete=result.status == "done",
            exit_reason=result.stop_reason or "exhausted",
            reported_count=result.counts.read,
        )

    async def pull_window(self, start: date, end: date) -> SourceBatch:
        """The job openings feed, pulled once (replayed from the ledger on re-entry)."""
        result = await self.call(Operation.JOB_OPENINGS, FeedArgs(domain=self.deps.domain))
        rows = []
        for record in result.records:
            if not isinstance(record, JobOpeningRecord):
                continue
            rows.append(
                RawPosting(
                    source_id=record.job_id,
                    title=record.title_text,
                    description=record.description_text,
                    url=record.url,
                    provider_code=record.provenance.provider_code.value,
                    raw_ref=record.provenance.raw_ref,
                    retrieved_at=record.provenance.retrieved_at.isoformat(),
                    posted_at=record.posted_at,
                    first_seen_at=record.first_seen_at,
                    status=record.status,
                    text_complete=None,  # VERIFY(V-JOBS-1): completeness not in the contract
                    location=record.location,
                    call_ref=result.call_refs[0] if result.call_refs else None,
                )
            )
        return SourceBatch(
            postings=tuple(rows),
            call_refs=tuple(result.call_refs),
            complete=result.status == "done",
            exit_reason=result.stop_reason or "exhausted",
        )


class ConnectorPageReader(_Calls):
    """``PageReadPort``: one plain scrape of a posting URL."""

    async def read(self, url: str, *, max_chars: int) -> PageText:
        """One plain scrape of a posting URL as returned."""
        result = await self.call(
            Operation.SCRAPE_JOB_PAGE, ScrapeJobPageArgs(url=url, page_kind="job_page")
        )
        record = next((r for r in result.records if isinstance(r, ScrapedPageRecord)), None)
        text = (record.markdown_text or "") if record else ""
        ok = bool(text) and (record is None or record.target_status in (None, 200))
        ref = result.call_refs[0] if result.call_refs else ""
        return PageText(text=text[:max_chars], complete=ok, call_ref=ref)


class RunPayloads:
    """``jobs.ports.PayloadStore`` over the platform payload store (neutral keys)."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps
        self.count = 0

    async def put_text(self, run_id: str, text: str) -> str:
        """Store posting text under a content-addressed neutral key."""
        import hashlib

        name = hashlib.sha256(text.encode()).hexdigest() + ".txt.gz"
        key = self.deps.payload_key("postings", name)
        await self.deps.payloads.put(key, text.encode())
        return str(key)


class TrackerEvidence:
    """``CoverageEvidencePort`` over the tracker (stage ``jobs`` owns the three rows)."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        """Report one owned row to the tracker (statuses are derived there)."""
        async with self.deps.txn(self.deps.tenant_id) as session:
            await self.deps.tracker(session).record_outcome(
                self.deps.tenant_id,
                self.deps.run_id,
                row_key,
                list(call_refs),
                Actor.STAGE,
                ReasonCode(not_attempted_reason)
                if (not call_refs and not_attempted_reason)
                else (None if call_refs else ReasonCode.NOT_AVAILABLE_YET),
                stage="jobs",
                expected_calls=expected_calls,
                cap_hit=cap_hit,
            )


class ProposalSink:
    """``GradingPort``: templated claims are stored as the ``jobs`` stage's proposals."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.reference: str | None = None

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        """Store the templated claims as the jobs stage's proposals."""
        if claims:
            self.reference = await self.store.put_stage(
                "jobs", [c.model_dump(mode="json") for c in claims]
            )


class RunJobsInput:
    """``RunInputPort`` from the run row: lanes switched off and the run's ``as_of`` date."""

    def __init__(self, lanes_off: frozenset[str], as_of: date) -> None:
        self._off, self._as_of = lanes_off, as_of

    async def lanes_off(self) -> frozenset[str]:
        """Lanes switched off in the run input."""
        return self._off

    async def as_of(self) -> date:
        """The run's as_of date (its creation date)."""
        return self._as_of


class LlmPostingClassifier:
    """The extractor (tier ``classifier``) or second pass (tier ``judge``) for one posting."""

    def __init__(self, deps: Any, tier: str, prompt_name: str) -> None:
        self.deps, self.tier, self.prompt_name = deps, tier, prompt_name

    async def __call__(self, posting_id: str, text: str, canary: str) -> object:
        import json

        from account_intelligence.agents.output import parse_json
        from account_intelligence.agents.prompts import prompt

        system = prompt(self.prompt_name).replace("{canary}", canary)
        user = json.dumps({"posting_id": posting_id, "text": text[:20_000]})
        result = await self.deps.llm.complete(self.tier, system, user, max_tokens=400)
        parsed = parse_json(result.text)
        return parsed if parsed is not None else result.text
