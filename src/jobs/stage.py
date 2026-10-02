"""Run the job pipeline once per run (pull once, J14) and hand its outputs on.

``run_jobs`` is what the workflow's ``jobs`` stage calls. It returns the stored record when the
run already has one (zero paid calls), otherwise runs one pass, stores each posting's text in the
payload store, persists the record, then gives the tracker its row evidence and the grading
engine its templated claims. Both hand-offs are idempotent, so a replay after a crash between
persist and checkpoint changes nothing.
"""

from __future__ import annotations

from jobs.pipeline import JobsPipeline, PipelineResult, replayed
from jobs.ports import CoverageEvidencePort, GradingPort, PayloadStore, PostingRepo, RunInputPort


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
    stored = await repo.load_run(run_id)
    if isinstance(stored, PipelineResult):
        result = replayed(stored)
    else:
        result = await pipeline.run_pass(
            run_id, await run_input.lanes_off(), await run_input.as_of()
        )
        result = await _store_texts(run_id, result, pipeline, payloads)
        await repo.save_run(run_id, result)
    for evidence in result.evidence:
        await coverage.record(
            evidence.row_key,
            evidence.call_refs,
            expected_calls=evidence.expected_calls,
            cap_hit=evidence.cap_hit,
            not_attempted_reason=evidence.not_attempted_reason,
        )
    await grading.submit_claims(result.claims)
    return result


async def _store_texts(
    run_id: str, result: PipelineResult, pipeline: JobsPipeline, payloads: PayloadStore
) -> PipelineResult:
    texts = pipeline.last_texts
    postings = []
    for posting in result.postings:
        text = texts.get(posting.posting_key, "")
        ref = await payloads.put_text(run_id, text) if text else None
        postings.append(posting.model_copy(update={"text_ref": ref}))
    from dataclasses import replace

    return replace(result, postings=tuple(postings))
