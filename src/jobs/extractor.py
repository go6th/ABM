"""Fail-closed validation for tool-less extractor output."""

from pydantic import BaseModel, ConfigDict, ValidationError

from jobs.errors import SchemaViolation
from jobs.models import ClassificationLabel


class ExtractorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    posting_id: str
    label: ClassificationLabel
    evidence_sentence: str


def validate_extractor_output(
    raw: object, *, posting_id: str, text: str, canary: str
) -> ExtractorResult:
    """Require exact schema, identity, verbatim evidence, and absent canary."""
    try:
        result = ExtractorResult.model_validate(raw)
    except ValidationError as exc:
        raise SchemaViolation("extractor_schema_invalid") from exc
    if result.posting_id != posting_id:
        raise SchemaViolation("extractor_posting_mismatch")
    if result.label == ClassificationLabel.UNCLEAR or result.evidence_sentence not in text:
        raise SchemaViolation("extractor_evidence_unverified")
    if canary and canary in result.evidence_sentence:
        raise SchemaViolation("extractor_canary_detected")
    return result
