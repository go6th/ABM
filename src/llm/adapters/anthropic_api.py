"""Anthropic SDK errors mapped to ``llm.errors`` (the API and Vertex AI clients raise the same).

The Anthropic port itself is ``account_intelligence.platform.llm.AnthropicLlm``; it may not depend
on the connectors (whose retryable error ``Overloaded`` extends), so the mapping lives here and
``workflow.deps.RunDeps`` applies it to every research call. Verified against the installed SDK
(anthropic 1.11.0, 2026-10-06): ``RateLimitError``, ``OverloadedError``,
``InternalServerError``, ``ServiceUnavailableError``, ``APITimeoutError``,
``APIConnectionError`` -> ``Overloaded``; ``NotFoundError`` -> ``ModelUnavailable``;
``RequestTooLargeError`` or a 400 whose message says the prompt is too long ->
``ContextTooLong``; ``AuthenticationError`` and ``PermissionDeniedError`` -> ``AuthFailed``; a
400 whose message says the credit balance is too low -> ``ProviderCreditsExhausted``.
The exact 400 wording for an over-long prompt is [VERIFY] V-LLM-3.

GUARANTEE(test_anthropic_adapter_maps_sdk_errors_to_typed_classes): mapping table.
"""

from __future__ import annotations

from llm.errors import (
    AuthFailed,
    ContextTooLong,
    ModelUnavailable,
    Overloaded,
    ProviderCreditsExhausted,
)


def map_error(exc: Exception) -> Exception:
    """The typed class for an SDK exception (unknown exceptions pass through unchanged)."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return AuthFailed("model provider refused the credentials")
    if isinstance(exc, anthropic.NotFoundError):
        return ModelUnavailable("model not found at the provider")
    if isinstance(exc, anthropic.RequestTooLargeError):
        return ContextTooLong("request too large")
    if isinstance(exc, anthropic.BadRequestError) and "too long" in str(exc).lower():
        return ContextTooLong("prompt is too long")
    if isinstance(exc, anthropic.BadRequestError) and "credit balance" in str(exc).lower():
        # observed 2026-10-12: a 400 invalid_request_error "Your credit balance is too low";
        # retrying cannot help, so the run keeps its pulls and ends partial
        return ProviderCreditsExhausted("model provider credits exhausted")
    retryable: tuple[type[Exception], ...] = (
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
    )
    for name in ("OverloadedError", "ServiceUnavailableError"):
        extra = getattr(anthropic, name, None)
        if isinstance(extra, type):
            retryable = (*retryable, extra)
    if isinstance(exc, retryable):
        return Overloaded("model overloaded or unavailable")
    return exc
