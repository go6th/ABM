"""Run the people pipeline once per run and hand its outputs on (the ``people`` stage body).

``run_people`` returns the stored record when the run already has one (zero paid calls),
otherwise runs one pass (search pages, profiles and posts are stored as they arrive, so a pass
resumed after a crash continues from the stored cursor and never re-pulls a stored profile),
registers each in-scope profile URL as a source with its stable code, persists the record, then
gives the tracker its row evidence and the grading engine its templated claims. Both hand-offs
are idempotent, so a replay after a crash between persist and checkpoint changes nothing.
"""

from __future__ import annotations

from dataclasses import replace

from people.pipeline import PeoplePipeline, PipelineResult, RunInputs, replayed
from people.ports import (
    CoverageEvidencePort,
    GradingPort,
    PeopleRepo,
    RunInputPort,
    SourceRegistryPort,
)


async def _allowed_domains(run_input: RunInputPort) -> tuple[str, ...]:
    """The entity lock's other domains; a run input without the method has none."""
    method = getattr(run_input, "allowed_domains", None)
    return tuple(await method()) if method is not None else ()


async def read_inputs(run_input: RunInputPort, account_domain: str) -> RunInputs:
    """The run inputs one pass uses, read once."""
    return RunInputs(
        account_domain=account_domain,
        as_of=await run_input.as_of(),
        lanes_off=await run_input.lanes_off(),
        regions=await run_input.regions(),
        reference_employers=await run_input.reference_employers(),
        budget_usd=await run_input.people_budget_usd(),
        excluded_people=await run_input.excluded_people(),
        allowed_domains=tuple(await _allowed_domains(run_input)),
    )


async def run_people(
    run_id: str,
    account_domain: str,
    pipeline: PeoplePipeline,
    repo: PeopleRepo,
    registry: SourceRegistryPort,
    coverage: CoverageEvidencePort,
    grading: GradingPort,
    run_input: RunInputPort,
) -> PipelineResult:
    """The stage body; see the module docstring for the guarantees."""
    stored = await repo.load_run(run_id)
    if isinstance(stored, PipelineResult):
        result = replayed(stored)
    else:
        result = await pipeline.run_pass(run_id, await read_inputs(run_input, account_domain))
        codes = {}
        for person in result.people:
            if person.status == "in_scope" and person.profile_url:
                codes[person.profile_url] = await registry.code_for(person.profile_url)
        result = replace(result, source_codes=codes)
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
