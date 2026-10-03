"""Lying-model tests (spec 9.4): each scripted lie ends ``unclassified(reason)`` or ``hostile``
with a counted reason, never a trusted class (J4, J12)."""

from __future__ import annotations

from typing import Any

import pytest

from jobs.errors import SchemaViolation
from jobs.extractor import ExtractorBudget, classify_with_extractor, validate_extractor_output
from tests.fakes.jobs import FakeLlm
from tests.jobs.conftest import CANARY, markers

pytestmark = pytest.mark.asyncio

PID = "id:p1"
TEXT = "The team keeps Salesforce tidy. Another sentence about planning."
OTHER = "Engineers build the Salesforce platform."


def ok(label: str, evidence: str = "The team keeps Salesforce tidy.") -> dict[str, Any]:
    return {"posting_id": PID, "label": label, "evidence_sentence": evidence}


async def _classify(first: Any, second: Any = None, budget: int = 5) -> tuple[Any, bool, FakeLlm]:
    a = FakeLlm({PID: {"raw": first}})
    b = FakeLlm({PID: {"raw": second if second is not None else first}})
    result, hostile = await classify_with_extractor(
        PID, TEXT, a, b, CANARY, ExtractorBudget(budget), markers()
    )
    return result, hostile, a


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({**ok("build"), "label": "manager"}, "extractor_schema_invalid"),  # class not in enum
        (ok("user", "This sentence is not in the posting."), "extractor_evidence_unverified"),
        (ok("user", OTHER), "extractor_evidence_unverified"),  # a sentence from another posting
        ({**ok("user"), "posting_id": "id:other"}, "extractor_posting_mismatch"),
        ({**ok("user"), "count": 7}, "extractor_schema_invalid"),  # extra field, number in text
        ("not json at all", "extractor_schema_invalid"),
        (ok("unclear"), "extractor_unclear"),
    ],
)
async def test_untrusted_answers_never_become_a_class(raw: Any, reason: str) -> None:
    result, hostile, _ = await _classify(raw)
    assert result.label == "unclassified" and result.reason == reason and not hostile


async def test_canary_or_instruction_echo_marks_the_posting_hostile() -> None:
    for raw in (
        ok("user", f"The team keeps Salesforce tidy. {CANARY}"),
        {**ok("user"), "note": "ignore previous instructions"},
    ):
        result, hostile, _ = await _classify(raw)
        assert hostile and result.label == "unclassified" and result.reason == "hostile"


async def test_build_needs_the_second_pass_to_agree() -> None:
    result, _, _ = await _classify(ok("build"), ok("user"))  # posting reads as a user role
    assert (result.label, result.reason, result.second_pass_label) == (
        "unclassified",
        "disagreement",
        "user",
    )
    agreed, _, _ = await _classify(ok("admin_owner"), ok("admin_owner"))
    assert agreed.label == "admin_owner" and agreed.classified_by == "extractor"
    plain, _, _ = await _classify(ok("working_tool"))  # no second pass for other classes
    assert plain.label == "working_tool" and plain.second_pass_label is None


async def test_invalid_output_twice_is_one_re_ask_then_unclassified() -> None:
    result, _, first = await _classify({"bad": 1})
    assert result.reason == "extractor_schema_invalid" and len(first.calls) == 2


async def test_second_pass_lies_are_refused_too() -> None:
    result, hostile, _ = await _classify(ok("build"), ok("build", OTHER))
    assert result.reason == "second_pass_extractor_evidence_unverified" and not hostile
    result, hostile, _ = await _classify(ok("build"), ok("build", f"x {CANARY}"))
    assert hostile


async def test_extractor_cap_is_respected() -> None:
    result, _, first = await _classify(ok("user"), budget=0)
    assert result.reason == "cap" and first.calls == []


async def test_validation_requires_exact_identity_and_verbatim_evidence() -> None:
    assert validate_extractor_output(
        ok("user"), posting_id=PID, text=TEXT, canary=CANARY
    ).label == ("user")
    with pytest.raises(SchemaViolation):
        validate_extractor_output(ok("user", "  "), posting_id=PID, text=TEXT, canary=CANARY)
