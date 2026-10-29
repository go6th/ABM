"""Run the job pipeline once per run (pull once, J14), then its bounded gap passes (spec 7).

``run_jobs`` is what the workflow's ``jobs`` stage calls:

1. If the run has a stored record (first pass or a later gap pass), it is replayed with zero
   paid calls. Otherwise one full pass runs, each posting's text goes to the payload store, and
   the record is persisted (``save_run``). An incremental re-research (``run_input.
   previous_postings()``, see ``jobs.incremental``) carries the previous delivered research's
   postings into the pass with their stored texts read back from the payload store; only the
   delta is pulled, read and classified.
2. Gap passes, at most ``max_gap_passes``: each re-processes only the remainder (``jobs.gap``)
   with texts read back from the payload store (no re-pull, no paid source call) inside the
   run-level caps, and is persisted as its own numbered pass (``save_pass``) before the next one
   starts. The loop ends ``converged`` (no remainder), ``no_progress``, ``budget`` (a cap is spent)
   or ``cap_reached`` (passes used up with a remainder left); the reason is in
   ``exit_reasons["gap"]``. A stored pass is never recomputed, so a resume continues where the
   kill happened.
3. The tracker gets the final row evidence and the grading engine the final templated claims.
   Both hand-offs are idempotent, so a replay after a crash between persist and checkpoint
   changes nothing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from crosscut.fanout import fan_out_all
from jobs.gap import gap_remainder
from jobs.pipeline import JobsPipeline, PipelineResult, replayed
from jobs.ports import CoverageEvidencePort, GradingPort, PayloadStore, PostingRepo, RunInputPort

FINAL_GAP_EXITS = frozenset({"converged", "no_progress", "budget", "cap_reached"})
# stored texts of carried postings read back side by side (the payload store, no paid call)
TEXT_CONCURRENCY = 16


async def run_jobs(
    run_id: str,
    pipeline: JobsPipeline,
    repo: PostingRepo,
    payloads: PayloadStore,
    coverage: CoverageEvidencePort,
    grading: GradingPort,
    run_input: RunInputPort,
) -> PipelineResult:
    """The stage body; see the module docstring for the guarantees."""
    lanes_off = await run_input.lanes_off()
    stored = await repo.load_run(run_id)
    calls = 0
    if isinstance(stored, PipelineResult):
        result = replayed(stored)
    else:
        previous = await run_input.previous_postings()
        carried_texts = await _carried_texts(previous, payloads)
        result = await pipeline.run_pass(
            run_id, lanes_off, await run_input.as_of(), previous, carried_texts
        )
        result = await _store_texts(run_id, result, pipeline, payloads)
        calls += result.calls_made
        await repo.save_run(run_id, result)
    max_passes = max(pipeline.cfg.caps.get("max_gap_passes", 0), 0)
    # LOOP(jobs gap passes): cap=max_gap_passes (spec 7: 2); exit=converged, no_progress, budget,
    # cap_reached; every pass is persisted before the next starts
    while result.exit_reasons.get("gap") not in FINAL_GAP_EXITS:
        if not gap_remainder(result.postings):
            result = _with_gap(result, "converged")
            break
        if result.pass_no >= max_passes:
            result = _with_gap(result, "cap_reached")
            break
        texts = {
            p.posting_key: await payloads.get_text(p.text_ref)
            for p in result.postings
            if p.text_ref
        }
        nxt = await pipeline.gap_pass(result, texts, lanes_off)
        if nxt.exit_reasons["gap"] == "pending" and nxt.pass_no >= max_passes:
            nxt = _with_gap(nxt, "cap_reached")
        nxt = await _store_texts(run_id, nxt, pipeline, payloads)
        calls += nxt.calls_made
        await repo.save_pass(run_id, nxt.pass_no, nxt)
        result = nxt
    for evidence in result.evidence:
        await coverage.record(
            evidence.row_key,
            evidence.call_refs,
            expected_calls=evidence.expected_calls,
            cap_hit=evidence.cap_hit,
            not_attempted_reason=evidence.not_attempted_reason,
        )
    await grading.submit_claims(result.claims)
    return replace(result, calls_made=calls)


def _with_gap(result: PipelineResult, reason: str) -> PipelineResult:
    return replace(result, exit_reasons={**result.exit_reasons, "gap": reason})


async def _carried_texts(previous: Any, payloads: PayloadStore) -> dict[str, str]:
    """The stored texts of the previous research's postings, read back from the payload store
    (no paid call) so an incremental pass can template their claims again. A copy that cannot be
    read back is left out: the posting is still carried, its claims are not re-made."""
    if previous is None:
        return {}
    refs = [(p.posting_key, p.text_ref) for p in previous.postings if p.text_ref]

    async def read(item: tuple[str, str]) -> tuple[str, str | None]:
        try:
            return item[0], await payloads.get_text(item[1])
        except Exception:  # noqa: BLE001 - a missing or unreadable copy only costs its claims
            return item[0], None

    # LOOP(carried text reads): cap=postings of the previous research, TEXT_CONCURRENCY in
    # flight; exit=all read (a failed read is skipped)
    return {
        key: body
        for key, body in await fan_out_all(refs, read, limit=TEXT_CONCURRENCY)
        if body is not None
    }


async def _store_texts(
    run_id: str, result: PipelineResult, pipeline: JobsPipeline, payloads: PayloadStore
) -> PipelineResult:
    """Store the text of every posting the pass processed; others keep their ``text_ref``."""
    texts = pipeline.last_texts
    postings = []
    for posting in result.postings:
        if posting.posting_key not in texts:
            postings.append(posting)
            continue
        text = texts[posting.posting_key]
        ref = await payloads.put_text(run_id, text) if text else None
        postings.append(posting.model_copy(update={"text_ref": ref}))
    return replace(result, postings=tuple(postings))
