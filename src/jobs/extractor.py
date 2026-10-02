"""Tool-less extractor wrapper and its verification (J4, J12).

Only the ambiguous remainder reaches the extractor. Its output is untrusted and must be exactly
``{posting_id, label, evidence_sentence}`` (no extra fields), with ``posting_id`` equal to the one
sent, ``label`` one of the four classes or ``unclear``, the evidence sentence verbatim in THIS
posting's stored text, and the canary absent. Schema failure gets one re-ask; anything else is
final. A Build or Admin and owner answer needs agreement from a second pass on a different model
tier (``JudgePort``); disagreement ends ``unclassified(disagreement)``. A canary or an echoed
instruction marks the posting hostile.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from jobs.errors import SchemaViolation
from jobs.models import Classification, ClassificationLabel
from jobs.normalise import strip_pii

AGREEMENT_CLASSES = frozenset({ClassificationLabel.BUILD, ClassificationLabel.ADMIN_OWNER})


class ExtractorResult(BaseModel):
    """The only shape an extractor answer may take."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    posting_id: str
    # a strict Literal accepts the JSON strings an LLM returns (a strict Enum would not)
    label: Literal["build", "admin_owner", "working_tool", "user", "unclear"]
    evidence_sentence: str

    @property
    def klass(self) -> ClassificationLabel:
        """The label as the enum."""
        return ClassificationLabel(self.label)


class HostileOutput(SchemaViolation):
    """The extractor echoed the canary or an instruction: the posting is hostile."""


def validate_extractor_output(
    raw: object, *, posting_id: str, text: str, canary: str, markers: tuple[str, ...] = ()
) -> ExtractorResult:
    """Exact schema, identity, verbatim evidence, no canary. Raises ``SchemaViolation``."""
    rendered = str(raw)
    if canary and canary in rendered:
        raise HostileOutput("extractor_canary_detected")
    if any(m.casefold() in rendered.casefold() for m in markers):
        raise HostileOutput("extractor_instruction_echo")
    try:
        result = ExtractorResult.model_validate(raw)
    except ValidationError as exc:
        raise SchemaViolation("extractor_schema_invalid") from exc
    if result.posting_id != posting_id:
        raise SchemaViolation("extractor_posting_mismatch")
    evidence = result.evidence_sentence.strip()
    if not evidence or evidence not in text:
        raise SchemaViolation("extractor_evidence_unverified")
    return result


Call = Callable[[str, str, str], Awaitable[object]]


@dataclass
class ExtractorBudget:
    """Run-level cap on first-pass extractor calls (second passes are bounded by these)."""

    remaining: int


async def classify_with_extractor(
    posting_id: str,
    text: str,
    first: Call,
    second: Call,
    canary: str,
    budget: ExtractorBudget,
    markers: tuple[str, ...] = (),
) -> tuple[Classification, bool]:
    """Classify an ambiguous posting. Returns ``(classification, hostile)``; never raises for
    untrusted output: every failure becomes ``unclassified(reason)``."""
    if budget.remaining <= 0:
        return _unclassified("cap"), False
    budget.remaining -= 1

    async def ask(call: Call) -> ExtractorResult:
        last: SchemaViolation | None = None
        for _ in range(2):  # one re-ask, only for a schema failure
            raw = await call(posting_id, text, canary)
            try:
                return validate_extractor_output(
                    raw, posting_id=posting_id, text=text, canary=canary, markers=markers
                )
            except HostileOutput:
                raise
            except SchemaViolation as exc:
                last = exc
                if str(exc) != "extractor_schema_invalid":
                    raise
        assert last is not None
        raise last

    try:
        result = await ask(first)
    except HostileOutput:
        return _unclassified("hostile"), True
    except SchemaViolation as exc:
        return _unclassified(str(exc)), False
    if result.klass is ClassificationLabel.UNCLEAR:
        return _unclassified("extractor_unclear"), False
    second_label: str | None = None
    if result.klass in AGREEMENT_CLASSES:
        try:
            check = await ask(second)
        except HostileOutput:
            return _unclassified("hostile"), True
        except SchemaViolation as exc:
            return _unclassified(f"second_pass_{exc}"), False
        second_label = check.label
        if check.label != result.label:
            return (
                Classification(
                    label="unclassified",
                    classified_by="extractor",
                    reason="disagreement",
                    second_pass_label=second_label,
                ),
                False,
            )
    return (
        Classification(
            label=result.label,
            classified_by="extractor",
            reason="extractor_verified",
            evidence_sentence=strip_pii(result.evidence_sentence.strip()),
            second_pass_label=second_label,
        ),
        False,
    )


def _unclassified(reason: str) -> Classification:
    return Classification(label="unclassified", classified_by="none", reason=reason)
