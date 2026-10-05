"""The job posting pipeline (PRD step 5) over injected ports. Spec: build_job_posting_pipeline.md.

Steps: (1) live board search, one call per lane keyword; (2) the 12-month feed pull, once; (3)
normalise, dedupe and window; (4) lexicon match; (5) read, with at most one bounded escalation
read per unread matched posting; (6) classify (rules, then the extractor with a second pass);
(7) full-text check; (8) counts from rows; (9) persist, then hand row evidence to the tracker and
templated claims to the grading engine.

Guarantees: pull once (a second ``run`` returns the stored record and makes zero calls, J14);
every loop is bounded and records its exit reason; one posting's failure never aborts the batch;
no count, class or status is typed by a caller or a model; untrusted text goes only to the
tool-less extractor and is never logged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date

from account_intelligence.grading.schema import ProposedSourced
from jobs.claims import claims_for
from jobs.classify import classify_rules
from jobs.config import JobsConfig
from jobs.counts import compute_counts
from jobs.dedupe import dedupe_counted, group_key
from jobs.errors import InvariantViolation, JobsError
from jobs.extractor import Call, ExtractorBudget, classify_with_extractor
from jobs.gap import gap_remainder, progress
from jobs.match import match_platforms
from jobs.models import (
    Classification,
    PageText,
    Posting,
    PostingCounts,
    RawPosting,
    SourceBatch,
    digest_text,
)
from jobs.normalise import canonical_url, clean_text, normalise_status, stored_text
from jobs.ports import PageReadPort, PostingSourcePort, SourceUnavailable
from jobs.sentences import extract_platform_sentences
from jobs.window import classify_window, window_start

ROW_BOARD = "job_board_by_lane"
ROW_PULL = "job_postings_12_months"
ROW_READ = "job_postings_read_classified"

_STOP_TO_EXIT = {
    "exhausted": "converged",
    "max_pages": "cap_reached",
    "max_items": "cap_reached",
    "max_bytes": "cap_reached",
    "deadline": "deadline",
    "loop_detected": "no_progress",
    "failed": "no_progress",
}


@dataclass(frozen=True)
class RowEvidence:
    """Evidence for one owned tracker row: ledger keys only (the tracker derives the status)."""

    row_key: str
    call_refs: tuple[str, ...]
    expected_calls: int
    cap_hit: bool
    not_attempted_reason: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    """Everything one pipeline pass produced; persisted as the run-level record."""

    run_id: str
    as_of: date
    window_start: date
    config_hash: str
    postings: tuple[Posting, ...]
    counts: PostingCounts | None
    invariant_error: str | None
    exit_reasons: dict[str, str]
    evidence: tuple[RowEvidence, ...]
    claims: tuple[ProposedSourced, ...]
    lanes_not_searched: dict[str, str]
    board_reported: dict[str, int | None]
    rejected_no_url: int
    claims_skipped_pii: int
    calls_made: int = 0
    replayed: bool = False
    # provenance of every record pulled (rejected ones included): ``pulled`` is its length, so a
    # gap pass recomputes the counts from records, never from a stored number (J1)
    pulled_refs: tuple[str, ...] = ()
    # run-level cap accounting across passes (spec 7: the caps bound the run, not a pass)
    page_reads_used: int = 0
    extractor_calls_used: int = 0
    pass_no: int = 0


@dataclass(frozen=True)
class _Frame:
    """What a pass's record inherits from the first pass (never changed by a gap pass)."""

    run_id: str
    as_of: date
    start: date
    pulled: tuple[str, ...]
    rejected: int
    pull_complete: bool
    lanes_off: frozenset[str]


@dataclass(frozen=True)
class _Used:
    """Run-level cap accounting: used before this pass and what was left when it started."""

    page_reads: int
    extractor_calls: int
    reads_left_at_start: int
    budget_left_at_start: int


@dataclass
class _Run:
    """Mutable working state of one pass (never leaves this module)."""

    calls: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    page_reads_left: int = 0
    escalation_cap_hit: bool = False
    escalation_refs: list[str] = field(default_factory=list)
    escalated: set[str] = field(default_factory=set)


class JobsPipeline:
    """Orchestrates the steps; every dependency is a port (spec 1)."""

    def __init__(
        self,
        cfg: JobsConfig,
        source: PostingSourcePort,
        pages: PageReadPort,
        extractor: Call,
        judge: Call,
        *,
        canary: str,
        hostile_markers: tuple[str, ...],
        is_hostile: Callable[[str], bool],
    ):
        self.cfg = cfg
        self.source = source
        self.pages = pages
        self.extractor = extractor
        self.judge = judge
        self.canary = canary
        self.markers = hostile_markers
        self.is_hostile = is_hostile
        # stored text of each posting from the last pass (persisted by ``jobs.stage``; never
        # logged, never put in the checkpoint)
        self.last_texts: dict[str, str] = {}

    # ---- step 1 and 2: sources (the only paid calls besides escalation reads) -------------

    async def _board(
        self, lanes_off: frozenset[str], w: _Run
    ) -> tuple[list[RawPosting], RowEvidence, dict[str, str], dict[str, int | None]]:
        postings: list[RawPosting] = []
        refs: list[str] = []
        not_searched: dict[str, str] = {}
        reported: dict[str, int | None] = {}
        searched = 0
        partial = False
        for lane in self.cfg.lanes:
            if lane.id in lanes_off:
                not_searched[lane.id] = "lane_not_in_scope"
                continue
            if not lane.keywords:
                not_searched[lane.id] = "not_available_yet"
                continue
            for keyword in dict.fromkeys(lane.keywords):  # no re-search of the same keyword
                searched += 1
                try:
                    batch = await self.source.search_board(lane.id, keyword)
                except SourceUnavailable as error:
                    if error.call_ref:
                        refs.append(error.call_ref)
                        w.failed.append(error.call_ref)
                    continue
                refs.extend(batch.call_refs)
                w.calls.extend(batch.call_refs)
                partial |= not batch.complete
                reported[f"{lane.id}:{keyword}"] = batch.reported_count
                postings.extend(
                    p.model_copy(update={"live_board_present": True}) for p in batch.postings
                )
        reason = None
        if not refs:
            all_off = all(lane.id in lanes_off for lane in self.cfg.lanes)
            reason = "lane_not_in_scope" if all_off else "not_available_yet"
        evidence = RowEvidence(ROW_BOARD, tuple(refs), max(searched, 1), partial, reason)
        return postings, evidence, not_searched, reported

    async def _feed(self, start: date, end: date, w: _Run) -> tuple[SourceBatch, RowEvidence]:
        try:
            batch = await self.source.pull_window(start, end)
        except SourceUnavailable as error:
            failed = (error.call_ref,) if error.call_ref else ()
            w.failed.extend(failed)
            return (
                SourceBatch(complete=False, exit_reason="failed", failed_refs=failed),
                RowEvidence(ROW_PULL, failed, 1, False, None if failed else "source_unavailable"),
            )
        w.calls.extend(batch.call_refs)
        evidence = RowEvidence(
            ROW_PULL, (*batch.call_refs, *batch.failed_refs), 1, not batch.complete
        )
        return batch, evidence

    # ---- steps 3 to 7: per posting ---------------------------------------------------------

    def _platforms_in_scope(self, lanes_off: frozenset[str]) -> dict[str, tuple[str, ...]]:
        return {
            name: aliases
            for lane in self.cfg.lanes
            if lane.id not in lanes_off
            for name, aliases in lane.platforms.items()
        }

    def _base(self, raw: RawPosting, as_of: date, refs: tuple[str, ...]) -> tuple[Posting, str]:
        caps = self.cfg.caps
        key = group_key(raw)
        text = stored_text(raw.description or "", caps["max_description_chars"])
        posted = _parse_date(raw.posted_at)
        first = _parse_date(raw.first_seen_at)
        if raw.live_board_present:
            status, status_source = "open", "live_board"  # a live observation beats the feed
        else:
            status = normalise_status(raw.status)
            status_source = "feed" if status != "unknown" else "unknown"
        posting = Posting(
            posting_key=key,
            weak_key=key.startswith("weak:"),
            title=clean_text(raw.title, caps["max_title_chars"]),
            location=clean_text(raw.location, caps["max_title_chars"]) if raw.location else None,
            url=canonical_url(raw.url),
            urls=tuple(sorted({canonical_url(raw.url), *raw.other_urls} - {""})),
            provider_code=raw.provider_code,
            raw_ref=raw.raw_ref,
            retrieved_at=raw.retrieved_at,
            posted_at=posted,
            first_seen_at=first,
            status=status,
            status_source=status_source,
            text_complete=raw.text_complete,
            window_bucket=classify_window(posted or first, as_of),
            call_refs=refs,
        )
        return posting, text

    def _is_read(self, complete: bool | None, text: str, hostile: bool) -> bool:
        return (
            complete is True and len(text) >= self.cfg.caps["minimum_text_length"] and not hostile
        )

    async def _escalate(self, posting: Posting, text: str, w: _Run) -> tuple[Posting, str]:
        """J5: one bounded page read of the posting URL for an unread matched posting."""
        missing = {"full_text_missing": True, "escalation": "failed"}
        if w.page_reads_left <= 0:
            w.escalation_cap_hit = True
            return posting.model_copy(update={**missing, "escalation": "cap"}), text
        w.page_reads_left -= 1
        w.escalated.add(posting.posting_key)
        try:
            page: PageText = await self.pages.read(
                posting.url, max_chars=self.cfg.caps["max_description_chars"]
            )
        except SourceUnavailable as error:
            if error.call_ref:
                w.failed.append(error.call_ref)
                w.escalation_refs.append(error.call_ref)
            return posting.model_copy(update=missing), text
        w.calls.append(page.call_ref)
        w.escalation_refs.append(page.call_ref)
        new_text = stored_text(page.text, self.cfg.caps["max_description_chars"])
        if not (page.complete and len(new_text) >= self.cfg.caps["minimum_text_length"]):
            return posting.model_copy(update=missing), text
        refs = (*posting.call_refs, page.call_ref)
        update = {"text_complete": True, "call_refs": refs, "escalation": "read"}
        return posting.model_copy(update=update), new_text

    async def _process(
        self,
        posting: Posting,
        text: str,
        lexicon: dict[str, tuple[str, ...]],
        budget: ExtractorBudget,
        w: _Run,
    ) -> tuple[Posting, str]:
        hostile = bool(text) and self.is_hostile(text)
        matches = match_platforms(posting.title, text, lexicon)
        matched = bool(matches.description or matches.title_only)
        unread = not self._is_read(posting.text_complete, text, False)
        if matched and not hostile and unread and posting.escalation == "none":
            posting, text = await self._escalate(posting, text, w)
            hostile = bool(text) and self.is_hostile(text)
            matches = match_platforms(posting.title, text, lexicon)
        read = self._is_read(posting.text_complete, text, hostile)
        sentences = (
            extract_platform_sentences(
                text, matches.description, lexicon, self.cfg.caps["max_sentence_chars"]
            )
            if read
            else ()
        )
        lanes = tuple(
            sorted({self.cfg.lane_of(p) for p in (*matches.description, *matches.title_only)})
        )
        posting = posting.model_copy(
            update={
                "platforms": matches.description,
                "title_platforms": matches.title_only,
                "lanes": lanes,
                "read": read,
                "full_text_read": read,
                "full_text_missing": posting.full_text_missing and not read,
                "hostile": hostile,
                "sentences": sentences,
                "text_sha256": digest_text(text) if text else None,
            }
        )
        if read and matches.description:
            classification, became_hostile = await self._classify(posting, text, budget)
            posting = posting.model_copy(
                update={
                    "classification": classification,
                    "hostile": became_hostile,
                    "read": not became_hostile,
                    "full_text_read": not became_hostile,
                    "sentences": () if became_hostile else posting.sentences,
                }
            )
        return posting, text

    async def _classify(
        self, posting: Posting, text: str, budget: ExtractorBudget
    ) -> tuple[Classification, bool]:
        verdict = classify_rules(text, self.cfg.rules)
        if verdict.label is not None:
            return (
                Classification(
                    label=verdict.label,
                    classified_by="rule",
                    reason=verdict.reason,
                    rule_id=verdict.rule_id,
                ),
                False,
            )
        return await classify_with_extractor(
            posting.posting_key, text, self.extractor, self.judge, self.canary, budget, self.markers
        )

    # ---- the whole pass ---------------------------------------------------------------------

    async def run_pass(self, run_id: str, lanes_off: frozenset[str], as_of: date) -> PipelineResult:
        """One full pass (the caller checks the store first; see ``run_once``)."""
        w = _Run(page_reads_left=self.cfg.caps["max_page_reads"])
        start = window_start(as_of)
        board, board_ev, lanes_not_searched, reported = await self._board(lanes_off, w)
        feed, pull_ev = await self._feed(start, as_of, w)
        raw = [*board, *feed.postings]
        merged = dedupe_counted(raw)
        lexicon = self._platforms_in_scope(lanes_off)
        budget = ExtractorBudget(self.cfg.caps["max_extractor_calls"])
        postings: list[Posting] = []
        texts: dict[str, str] = {}
        batch_size = max(self.cfg.caps["batch_size"], 1)
        # LOOP(posting batch loop): cap=postings pulled (bounded by the pull caps), batch_size per
        # batch; exit=all processed (one failure is isolated)
        for i in range(0, len(merged.rows), batch_size):
            for item in merged.rows[i : i + batch_size]:
                refs = merged.call_refs.get(group_key(item), ())
                base, text = self._base(item, as_of, refs)
                if base.window_bucket == "out_of_window":
                    # excluded and counted only: never read, escalated, classified or claimed
                    postings.append(base)
                    texts[base.posting_key] = ""
                    continue
                done, text = await self._isolated(base, text, lexicon, budget, w)
                postings.append(done)
                texts[done.posting_key] = text
        self.last_texts = texts
        pulled = tuple(r.raw_ref for r in raw)
        exits = {
            "board": "converged",
            "pull": _STOP_TO_EXIT.get(feed.exit_reason, "no_progress"),
        }
        return self._finish(
            _Frame(run_id, as_of, start, pulled, merged.rejected, feed.complete, lanes_off),
            postings,
            texts,
            w,
            budget,
            (board_ev, pull_ev),
            (),
            exits,
            lanes_not_searched,
            reported,
            _Used(0, 0, self.cfg.caps["max_page_reads"], self.cfg.caps["max_extractor_calls"]),
            0,
        )

    async def _isolated(
        self,
        posting: Posting,
        text: str,
        lexicon: dict[str, tuple[str, ...]],
        budget: ExtractorBudget,
        w: _Run,
    ) -> tuple[Posting, str]:
        """``_process`` with one posting's typed failure isolated (spec 7): it ends
        ``unclassified(error)`` and never aborts the batch. A page read it already spent is kept
        as ``failed`` so no later pass reads the posting again (one read per posting, J5)."""
        try:
            return await self._process(posting, text, lexicon, budget, w)
        except (JobsError, ValueError):
            spent = posting.posting_key in w.escalated
            return (
                posting.model_copy(
                    update={
                        "classification": Classification(
                            label="unclassified", classified_by="none", reason="error"
                        ),
                        "escalation": "failed" if spent else posting.escalation,
                    }
                ),
                text,
            )

    # ---- gap pass (spec 7) -------------------------------------------------------------------

    async def gap_pass(
        self, prior: PipelineResult, texts: dict[str, str], lanes_off: frozenset[str]
    ) -> PipelineResult:
        """Re-process only the remainder of ``prior`` (``jobs.gap``): no source call, no re-pull,
        within what is left of the RUN-level page-read and extractor caps. ``texts`` holds the
        stored text of every posting with a ``text_ref`` (read back from the payload store, no
        paid call). Returns the next pass's record with ``exit_reasons["gap"]`` set:
        ``converged`` (no remainder left), ``budget`` (no progress and a cap is spent),
        ``no_progress`` (no progress otherwise) or ``pending`` (progress, remainder left)."""
        caps = self.cfg.caps
        w = _Run(page_reads_left=max(caps["max_page_reads"] - prior.page_reads_used, 0))
        budget = ExtractorBudget(max(caps["max_extractor_calls"] - prior.extractor_calls_used, 0))
        start_reads, start_budget = w.page_reads_left, budget.remaining
        eligible = set(gap_remainder(prior.postings))
        lexicon = self._platforms_in_scope(lanes_off)
        postings: list[Posting] = []
        changed: dict[str, str] = {}
        # LOOP(gap pass over the remainder): cap=postings of the prior pass, run-level read and
        # extractor caps; exit=all remainder postings processed (one failure is isolated)
        for posting in prior.postings:
            if posting.posting_key not in eligible:
                postings.append(posting)
                continue
            reset = posting.model_copy(
                update={
                    "classification": None,
                    "read": False,
                    "full_text_read": False,
                    "full_text_missing": False,
                    "sentences": (),
                    "escalation": "none" if posting.escalation == "cap" else posting.escalation,
                }
            )
            done, text = await self._isolated(
                reset, texts.get(posting.posting_key, ""), lexicon, budget, w
            )
            postings.append(done)
            changed[done.posting_key] = text
        self.last_texts = changed
        all_texts = {**texts, **changed}
        moved = progress(prior.postings, postings)
        left = gap_remainder(postings)
        if not left:
            gap = "converged"
        elif moved:
            gap = "pending"
        else:
            gap = "budget" if start_reads == 0 or start_budget == 0 else "no_progress"
        frame = _Frame(
            prior.run_id,
            prior.as_of,
            prior.window_start,
            prior.pulled_refs,
            prior.rejected_no_url,
            prior.exit_reasons.get("pull") == "converged",
            lanes_off,
        )
        board_ev, pull_ev = (e for e in prior.evidence if e.row_key != ROW_READ)
        read_prior = next(e.call_refs for e in prior.evidence if e.row_key == ROW_READ)
        exits = {**prior.exit_reasons, "gap": gap}
        return self._finish(
            frame,
            postings,
            all_texts,
            w,
            budget,
            (board_ev, pull_ev),
            read_prior,
            exits,
            prior.lanes_not_searched,
            prior.board_reported,
            _Used(prior.page_reads_used, prior.extractor_calls_used, start_reads, start_budget),
            prior.pass_no + 1,
        )

    def _finish(
        self,
        frame: _Frame,
        postings: list[Posting],
        texts: dict[str, str],
        w: _Run,
        budget: ExtractorBudget,
        evidence: tuple[RowEvidence, RowEvidence],
        read_prior: tuple[str, ...],
        exits: dict[str, str],
        lanes_not_searched: dict[str, str],
        reported: dict[str, int | None],
        used: _Used,
        pass_no: int,
    ) -> PipelineResult:
        not_searched = {
            name: lanes_not_searched.get(lane.id, "lane_not_in_scope")
            for lane in self.cfg.lanes
            if lane.id in frame.lanes_off
            for name in lane.platforms
        }
        counts: PostingCounts | None = None
        error: str | None = None
        try:
            counts = compute_counts(frame.pulled, postings, list(self.cfg.lexicon), not_searched)
        except InvariantViolation as exc:
            error = str(exc)
        analysed = [p for p in postings if p.window_bucket != "out_of_window" and p.matched]
        shortfall = any(
            not p.hostile and (not p.read or (p.platforms and p.posting_class == "unclassified"))
            for p in analysed
        )
        read_refs = tuple(dict.fromkeys((*evidence[1].call_refs, *read_prior, *w.escalation_refs)))
        read_ev = RowEvidence(
            ROW_READ,
            read_refs,
            1,
            shortfall or error is not None or not frame.pull_complete,
            None if read_refs else "source_unavailable",
        )
        claims: list[ProposedSourced] = []
        skipped = 0
        for p in postings:
            made, n = claims_for(p, texts.get(p.posting_key, ""))
            claims.extend(made)
            skipped += n
        exits = {
            **exits,
            "escalation": "cap_reached" if w.escalation_cap_hit else "converged",
            "extractor": "cap_reached" if budget.remaining <= 0 else "converged",
        }
        return PipelineResult(
            run_id=frame.run_id,
            as_of=frame.as_of,
            window_start=frame.start,
            config_hash=self.cfg.config_hash,
            postings=tuple(postings),
            counts=counts,
            invariant_error=error,
            exit_reasons=exits,
            evidence=(*evidence, read_ev),
            claims=tuple(claims),
            lanes_not_searched=lanes_not_searched,
            board_reported=reported,
            rejected_no_url=frame.rejected,
            claims_skipped_pii=skipped,
            calls_made=len(w.calls) + len(w.failed),
            pulled_refs=frame.pulled,
            page_reads_used=used.page_reads + used.reads_left_at_start - w.page_reads_left,
            extractor_calls_used=used.extractor_calls
            + used.budget_left_at_start
            - budget.remaining,
            pass_no=pass_no,
        )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def replayed(result: PipelineResult) -> PipelineResult:
    """The stored result as returned on re-entry: zero calls made this time (J14)."""
    return replace(result, calls_made=0, replayed=True)


def result_to_json(result: PipelineResult) -> dict[str, object]:
    """Serialisable record (no posting text, no provider names; provider codes stay internal)."""
    return {
        "run_id": result.run_id,
        "as_of": result.as_of.isoformat(),
        "window_start": result.window_start.isoformat(),
        "config_hash": result.config_hash,
        "postings": [p.model_dump(mode="json") for p in result.postings],
        "counts": result.counts.model_dump(mode="json") if result.counts else None,
        "invariant_error": result.invariant_error,
        "exit_reasons": dict(result.exit_reasons),
        "evidence": [
            {
                "row_key": e.row_key,
                "call_refs": list(e.call_refs),
                "expected_calls": e.expected_calls,
                "cap_hit": e.cap_hit,
                "not_attempted_reason": e.not_attempted_reason,
            }
            for e in result.evidence
        ],
        "claims": [c.model_dump(mode="json") for c in result.claims],
        "lanes_not_searched": dict(result.lanes_not_searched),
        "board_reported": dict(result.board_reported),
        "rejected_no_url": result.rejected_no_url,
        "claims_skipped_pii": result.claims_skipped_pii,
        "pulled_refs": list(result.pulled_refs),
        "page_reads_used": result.page_reads_used,
        "extractor_calls_used": result.extractor_calls_used,
        "pass_no": result.pass_no,
    }


def result_from_json(data: dict[str, object]) -> PipelineResult:
    """Inverse of ``result_to_json`` (validated through the models)."""
    from typing import Any, cast

    d = cast(dict[str, Any], data)
    return PipelineResult(
        run_id=d["run_id"],
        as_of=date.fromisoformat(d["as_of"]),
        window_start=date.fromisoformat(d["window_start"]),
        config_hash=d["config_hash"],
        postings=tuple(Posting.model_validate(p) for p in d["postings"]),
        counts=PostingCounts.model_validate(d["counts"]) if d["counts"] else None,
        invariant_error=d["invariant_error"],
        exit_reasons=dict(d["exit_reasons"]),
        evidence=tuple(
            RowEvidence(
                e["row_key"],
                tuple(e["call_refs"]),
                e["expected_calls"],
                e["cap_hit"],
                e["not_attempted_reason"],
            )
            for e in d["evidence"]
        ),
        claims=tuple(ProposedSourced.model_validate_json(json.dumps(c)) for c in d["claims"]),
        lanes_not_searched=dict(d["lanes_not_searched"]),
        board_reported=dict(d["board_reported"]),
        rejected_no_url=d["rejected_no_url"],
        claims_skipped_pii=d["claims_skipped_pii"],
        pulled_refs=tuple(d["pulled_refs"]),
        page_reads_used=d["page_reads_used"],
        extractor_calls_used=d["extractor_calls_used"],
        pass_no=d["pass_no"],
    )
