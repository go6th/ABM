"""Distill writes the Summary's section 1 parts (2026-10-26, the reference Summary): what stands
out and the simplest way to sharpen it follow the narrative rule (state only statable facts),
a lane meaning names the lane of a fact it cites and labels its interpretation, how this could
be wrong follows the claim checks only. An output stored before the new parts still loads."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from agents.contracts import BudgetMeter, load_registry
from agents.distill.agent import distill
from agents.ports import RunContext
from agents.schemas import Claim, DistillOutput, LaneClaim, StoredFact

REG = load_registry()
T0 = datetime(2026, 10, 8, tzinfo=UTC)
CTX = RunContext(uuid4(), uuid4(), "a.example", "A", "a.example", False, "v")
FACTS = (
    StoredFact(
        fact_id="f-conf",
        row_key="company_news",
        attribute="leader",
        value="A new CIO",
        grade="confirmed",
        lane="salesforce",
        tier=1,
    ),
    StoredFact(
        fact_id="f-prob",
        row_key="company_news",
        attribute="program",
        value="A CRM program",
        grade="probable",
        lane="sap",
        tier=2,
    ),
)


class Model:
    def __init__(self, out: object) -> None:
        self.out = out

    async def call(self, *args: object) -> tuple[object, int]:
        return self.out, 1


def _distill(out: DistillOutput) -> tuple[DistillOutput, dict[str, int]]:
    result = asyncio.run(
        distill(REG["distill"], Model(out), BudgetMeter(REG["distill"], T0), CTX, FACTS, "public")
    )
    return result.output, dict(result.drops)


def test_an_output_stored_before_the_section_1_parts_still_loads() -> None:
    old = {"hypotheses": [], "questions": [], "narrative": [{"text": "x", "fact_ids": ["f"]}]}
    out = DistillOutput.model_validate(old)
    assert out.stands_out == () and out.lane_meanings == ()
    assert out.could_be_wrong == () and out.sharpen == ()


def test_section_1_parts_are_kept_or_dropped_by_their_rules() -> None:
    picture = Claim(text="The company makes medical devices.", fact_ids=("f-conf",))
    out = DistillOutput(
        narrative=(picture,),
        stands_out=(
            Claim(text="A new CIO took office.", fact_ids=("f-conf",)),
            Claim(text="A CRM program is running.", fact_ids=("f-prob",)),  # states a Probable
            Claim(text="Is a CRM program running?", fact_ids=("f-prob",)),
        ),
        lane_meanings=(
            LaneClaim(
                lane="salesforce",
                text="A new CIO arrived. Our interpretation: the CRM is being reviewed.",
                fact_ids=("f-conf",),
            ),
            LaneClaim(  # the lane of no cited fact
                lane="veeva",
                text="A new CIO arrived. Our interpretation: nothing changes.",
                fact_ids=("f-conf",),
            ),
            LaneClaim(  # interpretation not labeled
                lane="salesforce",
                text="A new CIO arrived and the CRM is being reviewed.",
                fact_ids=("f-conf",),
            ),
        ),
        could_be_wrong=(
            Claim(text="The program rests on one posting.", fact_ids=("f-prob",)),
            Claim(text="Unknown fact.", fact_ids=("f-nope",)),
        ),
        sharpen=(
            Claim(text="Ask who leads the CRM program.", fact_ids=("f-prob",)),  # states
            Claim(text="Who leads the CRM program?", fact_ids=("f-prob",)),
        ),
    )
    kept, drops = _distill(out)
    assert [c.text for c in kept.stands_out] == [
        "A new CIO took office.",
        "Is a CRM program running?",
    ]
    assert [(c.lane, c.text[:16]) for c in kept.lane_meanings] == [
        ("salesforce", "A new CIO arrive")
    ]
    assert [c.text for c in kept.could_be_wrong] == ["The program rests on one posting."]
    assert [c.text for c in kept.sharpen] == ["Who leads the CRM program?"]
    assert drops["lane_not_cited"] == 1
    assert drops["interpretation_unlabeled"] == 1
    assert drops["unconfirmed_stated"] == 2
    assert drops["unknown_fact"] == 1
