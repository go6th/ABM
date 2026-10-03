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
from collections.abc import Callable, Sequence
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


@dataclass
class _Run:
    """Mutable working state of one pass (never leaves this module)."""

    calls: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    page_reads_left: int = 0
    escalation_cap_hit: bool = False
    escalation_refs: list[str] = field(default_factory=list)


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
        if w.page_reads_left <= 0:
            w.escalation_cap_hit = True
            return posting.model_copy(update={"full_text_missing": True}), text
        w.page_reads_left -= 1
        try:
            page: PageText = await self.pages.read(
                posting.url, max_chars=self.cfg.caps["max_description_chars"]
            )
        except SourceUnavailable as error:
            if error.call_ref:
                w.failed.append(error.call_ref)
                w.escalation_refs.append(error.call_ref)
            return posting.model_copy(update={"full_text_missing": True}), text
        w.calls.append(page.call_ref)
        w.escalation_refs.append(page.call_ref)
        new_text = stored_text(page.text, self.cfg.caps["max_description_chars"])
        if not (page.complete and len(new_text) >= self.cfg.caps["minimum_text_length"]):
            return posting.model_copy(update={"full_text_missing": True}), text
        refs = (*posting.call_refs, page.call_ref)
        return posting.model_copy(update={"text_complete": True, "call_refs": refs}), new_text

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
        if matched and not hostile and not self._is_read(posting.text_complete, text, False):
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
                try:
                    done, text = await self._process(base, text, lexicon, budget, w)
                except (JobsError, ValueError):  # isolate one posting's failure (spec 7)
                    done = base.model_copy(
                        update={
                            "classification": Classification(
                                label="unclassified", classified_by="none", reason="error"
                            )
                        }
                    )
                postings.append(done)
                texts[done.posting_key] = text
        self.last_texts = texts
        return self._finish(
            run_id,
            as_of,
            start,
            raw,
            merged.rejected,
            postings,
            texts,
            w,
            budget,
            feed,
            (board_ev, pull_ev),
            lanes_off,
            lanes_not_searched,
            reported,
        )

    def _finish(
        self,
        run_id: str,
        as_of: date,
        start: date,
        raw: Sequence[RawPosting],
        rejected: int,
        postings: list[Posting],
        texts: dict[str, str],
        w: _Run,
        budget: ExtractorBudget,
        feed: SourceBatch,
        evidence: tuple[RowEvidence, RowEvidence],
        lanes_off: frozenset[str],
        lanes_not_searched: dict[str, str],
        reported: dict[str, int | None],
    ) -> PipelineResult:
        not_searched = {
            name: lanes_not_searched.get(lane.id, "lane_not_in_scope")
            for lane in self.cfg.lanes
            if lane.id in lanes_off
            for name in lane.platforms
        }
        counts: PostingCounts | None = None
        error: str | None = None
        try:
            counts = compute_counts(raw, postings, list(self.cfg.lexicon), not_searched)
        except InvariantViolation as exc:
            error = str(exc)
        analysed = [p for p in postings if p.window_bucket != "out_of_window" and p.matched]
        shortfall = any(
            not p.hostile and (not p.read or (p.platforms and p.posting_class == "unclassified"))
            for p in analysed
        )
        read_refs = tuple(dict.fromkeys((*evidence[1].call_refs, *w.escalation_refs)))
        read_ev = RowEvidence(
            ROW_READ,
            read_refs,
            1,
            shortfall or error is not None or not feed.complete,
            None if read_refs else "source_unavailable",
        )
        claims: list[ProposedSourced] = []
        skipped = 0
        for p in postings:
            made, n = claims_for(p, texts.get(p.posting_key, ""))
            claims.extend(made)
            skipped += n
        exits = {
            "board": "converged",
            "pull": _STOP_TO_EXIT.get(feed.exit_reason, "no_progress"),
            "escalation": "cap_reached" if w.escalation_cap_hit else "converged",
            "extractor": "cap_reached" if budget.remaining <= 0 else "converged",
        }
        return PipelineResult(
            run_id=run_id,
            as_of=as_of,
            window_start=start,
            config_hash=self.cfg.config_hash,
            postings=tuple(postings),
            counts=counts,
            invariant_error=error,
            exit_reasons=exits,
            evidence=(*evidence, read_ev),
            claims=tuple(claims),
            lanes_not_searched=lanes_not_searched,
            board_reported=reported,
            rejected_no_url=rejected,
            claims_skipped_pii=skipped,
            calls_made=len(w.calls) + len(w.failed),
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
    )
