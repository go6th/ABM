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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date

from account_intelligence.dev_limits import UNCAPPED
from account_intelligence.grading.schema import ProposedSourced
from crosscut.fanout import fan_out_all
from jobs.claims import claims_for
from jobs.classify import title_candidate
from jobs.config import JobsConfig
from jobs.counts import compute_counts
from jobs.dedupe import dedupe_counted, group_key
from jobs.errors import InvariantViolation, JobsError
from jobs.extractor import Call, ExtractorBudget, classify_with_extractor
from jobs.gap import gap_remainder, progress
from jobs.heuristic import classify_heuristic
from jobs.match import match_platforms
from jobs.models import (
    Classification,
    PageText,
    Posting,
    PostingCounts,
    PostingStatus,
    PreviousPostings,
    RawPosting,
    SourceBatch,
    StatusSource,
    digest_text,
)
from jobs.normalise import canonical_url, clean_text, normalise_status, stored_text, strip_pii
from jobs.ports import PageReadPort, PostingSourcePort, SourceUnavailable
from jobs.sentences import extract_platform_sentences
from jobs.window import classify_window, window_start

# postings read and classified at once (page reads and extractor calls are what each waits on)
JOBS_CONCURRENCY = 8
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
    # incremental re-research (``jobs.incremental``): the delivered run whose postings were
    # carried into this one without a pull, read or extractor call, and how many were carried
    previous_run_id: str | None = None
    carried: int = 0


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
    previous_run_id: str | None = None
    carried: int = 0


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
        searches: list[tuple[str, str]] = []
        for lane in self.cfg.lanes:
            if lane.id in lanes_off:
                not_searched[lane.id] = "lane_not_in_scope"
                continue
            if not lane.keywords:
                not_searched[lane.id] = "not_available_yet"
                continue
            # no re-search of the same keyword
            searches.extend((lane.id, keyword) for keyword in dict.fromkeys(lane.keywords))

        async def search_one(item: tuple[str, str]) -> SourceBatch | SourceUnavailable:
            try:
                return await self.source.search_board(*item)
            except SourceUnavailable as error:
                return error

        # the searches run side by side and are folded in below in the order they were listed
        for (lane_id, keyword), batch in zip(
            searches, await fan_out_all(searches, search_one), strict=True
        ):
            searched += 1
            if isinstance(batch, SourceUnavailable):
                if batch.call_ref:
                    refs.append(batch.call_ref)
                    w.failed.append(batch.call_ref)
                continue
            refs.extend(batch.call_refs)
            w.calls.extend(batch.call_refs)
            partial |= not batch.complete
            reported[f"{lane_id}:{keyword}"] = batch.reported_count
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

    def _base(
        self,
        raw: RawPosting,
        as_of: date,
        refs: tuple[str, ...],
        new_since_previous: bool | None = None,
    ) -> tuple[Posting, str]:
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
            last_seen=_parse_date(raw.last_seen_at),
            seniority=clean_text(raw.seniority, caps["max_title_chars"]) or None
            if raw.seniority
            else None,
            new_since_previous=new_since_previous,
        )
        return posting, text

    def _carry(
        self,
        stored: Posting,
        fresh: RawPosting | None,
        as_of: date,
        refs: tuple[str, ...],
        active: frozenset[str] | None,
    ) -> Posting:
        """A posting of the previous delivered research carried into this run: its text
        reference, sentences, class and read flags are kept (no read, no extractor call); the
        window bucket is recomputed for this ``as_of`` (a posting that fell out of the window stays
        a row, bucketed out of window); the status is refreshed by evidence only: a live board
        hit this run beats everything, then the feed's explicit "closed" on the fresh record, then
        the feed's active set (present: open, absent: closed), then the fresh record's own status;
        without any of them (the active pull failed and the delta did not return it) the stored
        status stays as it was."""
        status: PostingStatus = stored.status
        source: StatusSource = stored.status_source
        fresh_status = normalise_status(fresh.status) if fresh is not None else None
        if fresh is not None and fresh.live_board_present:
            status, source = "open", "live_board"
        elif fresh_status == "closed":
            status, source = "closed", "feed"
        elif active is not None:
            in_active = stored.posting_key in active or any(
                f"url:{u}" in active for u in stored.urls
            )
            status, source = ("open" if in_active else "closed"), "feed"
        elif fresh_status == "open":
            status, source = "open", "feed"
        update: dict[str, object] = {
            "status": status,
            "status_source": source,
            "window_bucket": classify_window(stored.posted_at or stored.first_seen_at, as_of),
            "new_since_previous": False,
        }
        if fresh is not None:
            caps = self.cfg.caps
            update.update(
                {
                    "retrieved_at": fresh.retrieved_at,
                    "raw_ref": fresh.raw_ref,
                    "call_refs": refs or stored.call_refs,
                    "last_seen": _parse_date(fresh.last_seen_at) or stored.last_seen,
                    "seniority": (
                        clean_text(fresh.seniority, caps["max_title_chars"]) or stored.seniority
                        if fresh.seniority
                        else stored.seniority
                    ),
                    "first_seen_at": stored.first_seen_at or _parse_date(fresh.first_seen_at),
                    "posted_at": stored.posted_at or _parse_date(fresh.posted_at),
                }
            )
        return stored.model_copy(update=update)

    async def _active(
        self, w: _Run, pull_ev: RowEvidence
    ) -> tuple[frozenset[str] | None, RowEvidence]:
        """The feed's open postings as a set of identities (provider id and canonical URLs) for
        the carried postings' status refresh; None when the call failed (statuses are kept), the
        failed reference cited on the pull row as tracker proof."""
        try:
            batch = await self.source.pull_active()
        except SourceUnavailable as error:
            failed = (error.call_ref,) if error.call_ref else ()
            w.failed.extend(failed)
            return None, replace(pull_ev, call_refs=(*pull_ev.call_refs, *failed), expected_calls=2)
        w.calls.extend(batch.call_refs)
        identities: set[str] = set()
        for p in batch.postings:
            if p.source_id:
                identities.add(f"id:{p.source_id}")
            identities.update(
                f"url:{u}" for u in (canonical_url(x) for x in (p.url, *p.other_urls)) if u
            )
        evidence = replace(
            pull_ev,
            call_refs=(*pull_ev.call_refs, *batch.call_refs, *batch.failed_refs),
            expected_calls=2,
            cap_hit=pull_ev.cap_hit or not batch.complete,
        )
        return frozenset(identities), evidence

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
        matches = match_platforms(posting.title, text, lexicon, self.cfg.case_sensitive_platforms)
        matched = bool(matches.description or matches.title_only)
        unread = not self._is_read(posting.text_complete, text, False)
        excerpt = self.cfg.read_excerpts and len(text) < self.cfg.caps["minimum_text_length"]
        wanted = (matched or excerpt) and bool(posting.url)
        if wanted and not hostile and unread and posting.escalation == "none":
            posting, text = await self._escalate(posting, text, w)
            hostile = bool(text) and self.is_hostile(text)
            matches = match_platforms(
                posting.title, text, lexicon, self.cfg.case_sensitive_platforms
            )
        read = self._is_read(posting.text_complete, text, hostile)
        sentences = (
            extract_platform_sentences(
                text,
                matches.description,
                lexicon,
                self.cfg.caps["max_sentence_chars"],
                self.cfg.case_sensitive_platforms,
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
        """The class of a read posting that names a platform in its description.

        Order (2026-10-29, reference parity): the TITLE rules only pick candidates; the tool-less
        extractor, verified by a second pass for Build and Admin and owner, is the one reader that
        may confirm a class, and it reads every posting when the caps are off (``UNCAPPED``) or
        else the candidates while its budget lasts. A posting the extractor did not read, or read
        without a verified class (cap, schema, unclear, disagreement), gets the reference's
        Working tool / User role rule over its platform sentences (``jobs.heuristic``), stored as
        ``classified_by="heuristic"`` with the reason. A hostile answer stays hostile."""
        unlimited = self.cfg.caps["max_extractor_calls"] >= UNCAPPED
        candidate = unlimited or title_candidate(posting.title, self.cfg.rules)
        why = "heuristic_window"
        if candidate and budget.remaining > 0:
            verdict, hostile = await classify_with_extractor(
                posting.posting_key,
                text,
                self.extractor,
                self.judge,
                self.canary,
                budget,
                self.markers,
            )
            if hostile or verdict.label != "unclassified":
                return verdict, hostile
            why = f"heuristic_after_{verdict.reason}"
        elif candidate:
            why = "heuristic_after_cap"
        found = classify_heuristic(
            text,
            posting.platforms,
            self.cfg.lexicon,
            self.cfg.heuristic,
            self.cfg.case_sensitive_platforms,
        )
        return (
            Classification(
                label=found.label,
                classified_by="heuristic",
                reason=why,
                evidence_sentence=strip_pii(found.evidence)[:600] or None,
            ),
            False,
        )

    # ---- the whole pass ---------------------------------------------------------------------

    async def run_pass(
        self,
        run_id: str,
        lanes_off: frozenset[str],
        as_of: date,
        previous: PreviousPostings | None = None,
        carried_texts: Mapping[str, str] | None = None,
    ) -> PipelineResult:
        """One full pass (the caller checks the store first; see ``run_once``).

        With ``previous`` (an incremental re-research, ``jobs.incremental``) every posting of the
        previous delivered research is carried by posting key with no read and no extractor call;
        the feed pull is the delta the adapter was given; only keys not in the previous set are
        processed, flagged ``new_since_previous``; one ``pull_active`` call refreshes the carried
        statuses. ``carried_texts`` holds the carried postings' stored texts (payload store, no
        paid call) so their claims are templated again for this run."""
        w = _Run(page_reads_left=self.cfg.caps["max_page_reads"])
        start = window_start(as_of)
        board, board_ev, lanes_not_searched, reported = await self._board(lanes_off, w)
        feed, pull_ev = await self._feed(start, as_of, w)
        carried = {p.posting_key: p for p in previous.postings} if previous else {}
        by_url = {f"url:{u}": p.posting_key for p in carried.values() for u in p.urls}
        active: frozenset[str] | None = None
        if previous is not None:
            active, pull_ev = await self._active(w, pull_ev)
        raw = [*board, *feed.postings]
        merged = dedupe_counted(raw)
        lexicon = self._platforms_in_scope(lanes_off)
        budget = ExtractorBudget(self.cfg.caps["max_extractor_calls"])
        postings: list[Posting] = []
        texts: dict[str, str] = {}
        flag = True if previous is not None else None

        def stored_for(item: RawPosting) -> Posting | None:
            """The carried posting a fresh record is (by key, else by any shared URL)."""
            key = group_key(item)
            if key in carried:
                return carried[key]
            for u in (canonical_url(x) for x in (item.url, *item.other_urls)):
                if u and f"url:{u}" in by_url:
                    return carried[by_url[f"url:{u}"]]
            return None

        async def handle(item: RawPosting) -> tuple[Posting, str | None]:
            """One posting built, read and classified (out-of-window ones are only counted); a
            carried posting is refreshed only (text None: nothing new to store)."""
            refs = merged.call_refs.get(group_key(item), ())
            stored = stored_for(item)
            if stored is not None:
                return self._carry(stored, item, as_of, refs, active), None
            base, text = self._base(item, as_of, refs, flag)
            if base.window_bucket == "out_of_window":
                # excluded and counted only: never read, escalated, classified or claimed
                return base, ""
            return await self._isolated(base, text, lexicon, budget, w)

        # LOOP(posting fan-out): cap=postings pulled (bounded by the pull caps), JOBS_CONCURRENCY
        # in flight; exit=all processed (one failure is isolated). The page-read and extractor
        # budgets are taken before a call starts, so side by side they cap as one at a time did.
        for done, text in await fan_out_all(merged.rows, handle, limit=JOBS_CONCURRENCY):
            postings.append(done)
            if text is not None:
                texts[done.posting_key] = text
        seen = {p.posting_key for p in postings}
        # every previous posting the delta did not return is carried as stored (nothing is ever
        # deleted); its own provenance counts as pulled so the count chain holds
        kept = [
            self._carry(p, None, as_of, p.call_refs, active)
            for key, p in carried.items()
            if key not in seen
        ]
        postings.extend(kept)
        self.last_texts = texts  # carried postings keep their stored text reference
        pulled = (*(r.raw_ref for r in raw), *(p.raw_ref for p in kept))
        exits = {
            "board": "converged",
            "pull": _STOP_TO_EXIT.get(feed.exit_reason, "no_progress"),
        }
        all_texts = {**(carried_texts or {}), **texts}
        return self._finish(
            _Frame(
                run_id,
                as_of,
                start,
                pulled,
                merged.rejected,
                feed.complete,
                lanes_off,
                previous.run_id if previous is not None else None,
                len(carried),
            ),
            postings,
            all_texts,
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
            prior.previous_run_id,
            prior.carried,
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
            previous_run_id=frame.previous_run_id,
            carried=frame.carried,
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
        "previous_run_id": result.previous_run_id,
        "carried": result.carried,
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
        # records written before the incremental fields existed have neither key
        previous_run_id=d.get("previous_run_id"),
        carried=int(d.get("carried") or 0),
    )
