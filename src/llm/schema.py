"""Output validation in code (R3): every response is parsed and validated against its pydantic
schema regardless of any provider-native structured mode. Nothing unvalidated reaches a caller.

GUARANTEE(test_schema_validation_refuses_every_malformed_output): R3.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from account_intelligence.agents.output import parse_json

M = TypeVar("M", bound=BaseModel)


def validate(text: str, schema: type[M]) -> M | None:
    """The validated model, or None when the text is not JSON or does not fit the schema."""
    parsed = parse_json(text)
    if not isinstance(parsed, dict):
        return None
    try:
        return schema.model_validate(parsed)
    except ValidationError:
        return None


REASK_NOTE = "\nYour previous answer did not match the required JSON schema. Answer with JSON only."
