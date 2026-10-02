"""Helpers shared by provider connectors: JSON decode, schema validation, provenance."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from account_intelligence.connectors.base import Operation, ProviderCode, RawResponse
from account_intelligence.connectors.errors import (
    AuthError,
    ConnectorError,
    CreditsExhausted,
    NonRetryableProviderError,
    RateLimited,
    RetryableProviderError,
    SchemaDriftError,
)
from account_intelligence.connectors.models import Provenance
from account_intelligence.connectors.ratelimit import parse_retry_after

M = TypeVar("M", bound=BaseModel)


class Strict(BaseModel):
    """Response validation base: fields we rely on are declared; extra fields are ignored
    here and survive only in the stored raw payload."""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


def decode_json(raw: RawResponse, code: ProviderCode, op: Operation) -> Any:
    try:
        return json.loads(raw.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaDriftError(
            "body is not valid JSON", provider_code=code, operation=op, http_status=raw.status_code
        ) from error


def validate(model: type[M], data: Any, code: ProviderCode, op: Operation) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in error.errors()})[:10]
        raise SchemaDriftError(
            f"response failed validation at: {', '.join(fields)}", provider_code=code, operation=op
        ) from error


def provenance(
    base: Mapping[str, Any], *, source_url: str | None, page: int, index: int
) -> Provenance:
    return Provenance(
        provider_code=base["provider_code"],
        operation=base["operation"],
        source_url=source_url,
        retrieved_at=base["retrieved_at"],
        raw_ref=base["raw_ref"],
        page=page,
        index=index,
    )


def classify_status(
    raw: RawResponse,
    code: ProviderCode,
    op: Operation,
    *,
    auth: frozenset[int] = frozenset({401}),
    credits: frozenset[int] = frozenset({402}),
    rate_limited: frozenset[int] = frozenset({429}),
    retryable: frozenset[int] = frozenset({408}),
) -> ConnectorError:
    """Map a non-2xx status to the typed hierarchy using the provider's sourced table."""
    status = raw.status_code
    kw: dict[str, Any] = {"provider_code": code, "operation": op, "http_status": status}
    if status in rate_limited:
        return RateLimited(
            f"http {status}", retry_after=parse_retry_after(raw.headers.get("retry-after")), **kw
        )
    if status in auth:
        return AuthError(f"http {status}", **kw)
    if status in credits:
        return CreditsExhausted(f"http {status}", **kw)
    if status in retryable or status >= 500:
        return RetryableProviderError(f"http {status}", **kw)
    return NonRetryableProviderError(f"http {status}", **kw)
