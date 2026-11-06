"""Distill keeps a figure only when a cited fact's value or event date carries it (the Summary's
B6 rule over the cited rows), and asks the model once more when any claim was cut; the attempt
that keeps a narrative and the most claims is the result."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from agents.contracts import BudgetMeter, load_registry
from agents.distill.agent import distill, unregistered_figure
from agents.ports import RunContext
from agents.schemas import Claim, DistillOutput, StoredFact

REG = load_registry()
T0 = datetime(2026, 10, 8, tzinfo=UTC)
CTX = RunContext(uuid4(), uuid4(), "a.example", "A", "a.example", False, "v")
FACTS = (
    StoredFact(
        fact_id="f-plant",
        row_key="company_news",
        attribute="site",
        value="A new plant with 1,200 staff",
        grade="confirmed",
        lane="sap",
        tier=1,
        event_date="2025-03-04",
    ),
    StoredFact(
        fact_id="f-cio",
        row_key="company_news",
        attribute="leader",
        value="A new CIO",
        grade="confirmed",
        lane="salesforce",
        tier=1,
    ),
)


class Model:
    """Answers in order; the last answer repeats. Records every input it was given."""

    def __init__(self, *outs: DistillOutput) -> None:
        self.outs, self.inputs = list(outs), []

    async def call(self, *args: object) -> tuple[object, int]:
        # the runtime hands the model the input as JSON (``payload.model_dump``)
        self.inputs.append(next(a for a in args if isinstance(a, dict)))
        out = self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]
        return out, 1


def _run(model: Model) -> tuple[DistillOutput, dict[str, int]]:
    result = asyncio.run(
        distill(REG["distill"], model, BudgetMeter(REG["distill"], T0), CTX, FACTS, "public")
    )
    return result.output, dict(result.drops)


def test_figures_are_judged_against_the_cited_facts_texts() -> None:
    cited = ("A new plant with 1,200 staff", "2025-03-04")
    assert not unregistered_figure(
        Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)), cited
    )
    assert not unregistered_figure(Claim(text="It has 1200 staff.", fact_ids=("f-plant",)), cited)
    assert not unregistered_figure(
        Claim(text="Opened on 4 March 2025.", fact_ids=("f-plant",)), cited
    )
    assert unregistered_figure(Claim(text="It has 12 plants.", fact_ids=("f-plant",)), cited)
    assert unregistered_figure(Claim(text="Staff grew 30%.", fact_ids=("f-plant",)), cited)
    # a source code is a citation, a glued token is not a figure
    assert not unregistered_figure(Claim(text="See S-0001 in Q3.", fact_ids=("f-plant",)), cited)


def test_a_claim_with_a_made_up_figure_is_dropped_and_the_model_is_asked_once_more() -> None:
    first = DistillOutput(
        narrative=(
            Claim(text="The plant opened in 2025 with 1,200 staff.", fact_ids=("f-plant",)),
            Claim(text="The company runs 12 plants.", fact_ids=("f-plant",)),
        ),
        stands_out=(Claim(text="A new CIO took office.", fact_ids=("f-cio",)),),
    )
    model = Model(first)
    kept, drops = _run(model)
    assert [c.text for c in kept.narrative] == ["The plant opened in 2025 with 1,200 staff."]
    # the same answer came back, so the figure was dropped on both attempts
    assert drops["unregistered_number"] == 2 and drops["claims_reasked"] == 1
    assert "narrative_reasked" not in drops
    # the one re-ask carries the reasons and the figure rule; the first call carried no notes
    assert model.inputs[0]["notes"] is None and len(model.inputs) == 2
    assert "unregistered_number (1)" in (model.inputs[1]["notes"] or "")
    assert "value or event_date" in (model.inputs[1]["notes"] or "")


def test_the_attempt_that_keeps_more_is_the_result() -> None:
    first = DistillOutput(
        narrative=(
            Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)),
            Claim(text="The company runs 12 plants.", fact_ids=("f-plant",)),
        ),
        stands_out=(
            Claim(text="A new CIO took office.", fact_ids=("f-cio",)),
            Claim(text="3 CIOs came and went.", fact_ids=("f-cio",)),
        ),
    )
    mended = DistillOutput(
        narrative=(
            Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)),
            Claim(text="The company runs several plants.", fact_ids=("f-plant",)),
        ),
        stands_out=(
            Claim(text="A new CIO took office.", fact_ids=("f-cio",)),
            Claim(text="Several CIOs came and went.", fact_ids=("f-cio",)),
        ),
    )
    kept, drops = _run(Model(first, mended))
    assert len(kept.narrative) == 2 and len(kept.stands_out) == 2
    assert drops["unregistered_number"] == 2 and drops["claims_reasked"] == 1  # the first attempt's


def test_a_worse_second_answer_does_not_replace_the_first() -> None:
    first = DistillOutput(
        narrative=(
            Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)),
            Claim(text="The company runs 12 plants.", fact_ids=("f-plant",)),
        ),
    )
    worse = DistillOutput(narrative=(Claim(text="Twelve of 12 plants.", fact_ids=("f-plant",)),))
    kept, drops = _run(Model(first, worse))
    assert [c.text for c in kept.narrative] == ["The plant opened in 2025."]
    assert drops["unregistered_number"] == 2  # one on each attempt


def test_a_drop_for_another_reason_alone_does_not_ask_twice() -> None:
    out = DistillOutput(
        narrative=(Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)),),
        could_be_wrong=(Claim(text="Unknown fact.", fact_ids=("f-nope",)),),
    )
    model = Model(out)
    kept, drops = _run(model)
    assert len(model.inputs) == 1 and drops == {"unknown_fact": 1} and len(kept.narrative) == 1


def test_a_clean_answer_is_not_asked_twice() -> None:
    clean = DistillOutput(
        narrative=(Claim(text="The plant opened in 2025.", fact_ids=("f-plant",)),)
    )
    model = Model(clean)
    kept, drops = _run(model)
    assert len(model.inputs) == 1 and drops == {} and len(kept.narrative) == 1
