"""The Sense-check agent (G6, D-96).

It examines stored facts for the four kinds of problem the skill names (guide S10) and recommends
targeted pulls (S18). Code validates the output:

* finding kinds must be the configured kinds; every fact id must be one it was given; a
  contradiction must name at least two facts (those become Conflicting through the grading
  engine, never edited here);
* a proposal must name a planned row; it is priced in code through the credit port and routed by
  ``guards.spend_route`` (run if the approved cap covers it, otherwise pause or record).

It never builds, spends or changes a fact itself.

GUARANTEE(test_sense_check_lies_are_filtered)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from agents.config import SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import OutputRejected
from agents.ports import AgentModel, RunContext
from agents.runtime import call_agent
from agents.schemas import PullProposal, SenseCheckInput, SenseCheckOutput, SenseFinding


@dataclass
class SenseResult:
    """Validated findings and proposals."""

    findings: list[SenseFinding] = field(default_factory=list)
    proposals: list[PullProposal] = field(default_factory=list)
    drops: Counter[str] = field(default_factory=Counter)

    @property
    def contradictions(self) -> list[SenseFinding]:
        """Findings that make facts Conflicting."""
        return [f for f in self.findings if f.kind == "contradictory_fact"]


async def sense_check(
    contract: AgentContract,
    model: AgentModel,
    meter: BudgetMeter,
    ctx: RunContext,
    inp: SenseCheckInput,
    cfg: SkillConfig,
) -> SenseResult:
    """Run and validate one sense check (see the module docstring)."""
    result = SenseResult()
    try:
        out = await call_agent(contract, model, meter, ctx, inp, client_bound=False)
    except OutputRejected as rejected:
        result.drops[f"output_{rejected.code}"] += 1
        return result
    assert isinstance(out, SenseCheckOutput)
    known = {f.fact_id for f in inp.facts}
    for finding in out.findings:
        if finding.kind not in cfg.sense_kinds:
            result.drops["unknown_kind"] += 1
        elif not set(finding.fact_ids) <= known:
            result.drops["unknown_fact"] += 1
        elif finding.kind == "contradictory_fact" and len(set(finding.fact_ids)) < 2:
            result.drops["contradiction_needs_two_facts"] += 1
        else:
            result.findings.append(finding)
    seen: set[tuple[str, str]] = set()
    for proposal in out.proposals:
        key = (proposal.row_key, proposal.query.casefold())
        if proposal.row_key not in cfg.plan:
            result.drops["unknown_row"] += 1
        elif key in seen:
            result.drops["duplicate_proposal"] += 1
        else:
            seen.add(key)
            result.proposals.append(proposal)
    return result
