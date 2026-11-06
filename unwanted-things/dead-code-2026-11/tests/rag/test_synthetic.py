"""Synthetic evaluation datasets: rules and diversity selection."""

from __future__ import annotations

import pytest

from account_intelligence.rag.synthetic import (
    SyntheticExample,
    contains_pii,
    filter_valid,
    is_answerable,
    is_self_referential,
    scenario_balance,
    select_diverse,
    validate,
)


def _example(
    question: str = "what is the acme revenue?",
    answer: str = "the acme revenue grew",
    context: str = "the acme revenue grew in 2026",
    scenario: str = "happy_path",
) -> SyntheticExample:
    """A valid synthetic example."""
    return SyntheticExample(
        question=question,
        answer=answer,
        context=context,
        scenario=scenario,  # type: ignore[arg-type]
    )


class TestRules:
    def test_self_referential(self) -> None:
        """Questions that lean on the prompt are rejected."""
        assert is_self_referential("based on the provided text, what is the revenue?")
        assert not is_self_referential("what is the acme revenue?")

    def test_contains_pii_email(self) -> None:
        """Email addresses are PII."""
        assert contains_pii("contact ada@acme.com")

    def test_contains_pii_phone(self) -> None:
        """Phone numbers are PII."""
        assert contains_pii("call +1 555 123 4567")

    def test_contains_pii_ssn_shape(self) -> None:
        """SSN-shaped numbers are PII."""
        assert contains_pii("id 123-45-6789")

    def test_contains_pii_clean(self) -> None:
        """Ordinary text carries no PII."""
        assert not contains_pii("the acme revenue grew in 2026")

    def test_answerable(self) -> None:
        """An answer whose terms appear in the context is answerable."""
        assert is_answerable(_example())

    def test_unanswerable(self) -> None:
        """An answer sharing nothing with the context is unanswerable."""
        assert not is_answerable(_example(answer="the zeta quota shrank"))

    def test_validate_clean(self) -> None:
        """A valid example violates no rule."""
        assert validate(_example()) == ()

    def test_validate_collects_violations(self) -> None:
        """Every violated rule is reported."""
        bad = _example(
            question="based on the provided text, email ada@acme.com",
            answer="the zeta quota",
        )
        assert set(validate(bad)) == {
            "self_referential",
            "pii",
            "unanswerable",
        }

    def test_validate_empty_question(self) -> None:
        """An empty question is a violation."""
        assert "empty_question" in validate(_example(question="   "))

    def test_filter_valid(self) -> None:
        """Only clean examples survive."""
        examples = (_example(), _example(answer="the zeta quota"))
        assert filter_valid(examples) == (examples[0],)

    def test_scenario_balance(self) -> None:
        """The coverage matrix counts each scenario."""
        examples = (
            _example(scenario="happy_path"),
            _example(scenario="edge_case"),
            _example(scenario="edge_case"),
            _example(scenario="adversarial"),
        )
        assert scenario_balance(examples) == {
            "happy_path": 1,
            "edge_case": 2,
            "adversarial": 1,
        }


class TestDiversity:
    def test_select_diverse_spreads_topics(self) -> None:
        """The farthest points are picked first."""
        examples = (
            _example(question="q1 revenue"),
            _example(question="q2 revenue"),
            _example(question="q3 headcount"),
        )
        embeddings = (
            [1.0, 0.0, 0.0],
            [0.99, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        )
        picked = select_diverse(examples, embeddings, count=2)
        assert picked[0] is examples[0]
        assert picked[1] is examples[2]

    def test_select_diverse_count_zero(self) -> None:
        """A count of zero selects nothing."""
        assert select_diverse((_example(),), ([1.0],), count=0) == ()

    def test_select_diverse_capped_by_pool(self) -> None:
        """The pool bounds the selection."""
        picked = select_diverse((_example(),), ([1.0],), count=5)
        assert len(picked) == 1

    def test_select_diverse_misaligned_raises(self) -> None:
        """Examples and embeddings must align."""
        with pytest.raises(ValueError):
            select_diverse((_example(),), ([1.0], [0.0]), count=1)

    def test_select_diverse_negative_raises(self) -> None:
        """A negative count is a caller error."""
        with pytest.raises(ValueError):
            select_diverse((), (), count=-1)
