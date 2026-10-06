"""Anthropic Messages adapter (API and Vertex AI clients share ``messages.create``).

Verified against the installed SDK (anthropic 1.11.0, 2026-10-06): ``messages.create`` takes
``model``, ``max_tokens``, ``system``, ``messages`` and a top-level ``cache_control``; it has no
``temperature`` parameter (OI-LLM-1), so a configured temperature is refused rather than silently
dropped. Clients are built with ``max_retries=0`` (W6). Errors map to ``llm.errors``:
``RateLimitError``, ``OverloadedError``, ``InternalServerError``, ``ServiceUnavailableError``,
``APITimeoutError``, ``APIConnectionError`` -> ``Overloaded``; ``NotFoundError`` ->
``ModelUnavailable``; ``RequestTooLargeError`` or a 400 whose message says the prompt is too long
-> ``ContextTooLong``; ``AuthenticationError`` and ``PermissionDeniedError`` -> ``AuthFailed``.
The exact 400 wording for an over-long prompt is [VERIFY] V-LLM-3.

GUARANTEE(test_anthropic_adapter_maps_sdk_errors_to_typed_classes): mapping table.
"""

from __future__ import annotations

from typing import Any

from llm.config import Candidate
from llm.errors import AuthFailed, ContentRefused, ContextTooLong, ModelUnavailable, Overloaded
from llm.models import AdapterReply, Usage


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


class AnthropicAdapter:
    """``ModelAdapter`` over an ``AsyncAnthropic`` or ``AsyncAnthropicVertex`` client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def complete(
        self,
        candidate: Candidate,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float | None,
        cache_prefix: bool,
    ) -> AdapterReply:
        """One call; typed errors only."""
        if temperature is not None:
            raise ValueError("the installed SDK takes no temperature (OI-LLM-1)")
        kwargs: dict[str, Any] = {
            "model": candidate.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if cache_prefix:
            kwargs["cache_control"] = {"type": "ephemeral"}
        try:
            message = await self.client.messages.create(**kwargs)
        except Exception as exc:
            mapped = map_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        if getattr(message, "stop_reason", None) == "refusal":
            raise ContentRefused("the model refused")
        text = "".join(
            str(getattr(b, "text", "")) for b in message.content if getattr(b, "type", "") == "text"
        )
        u = message.usage
        return AdapterReply(
            text=text,
            model=str(message.model),
            usage=Usage(
                int(u.input_tokens),
                int(u.output_tokens),
                int(getattr(u, "cache_read_input_tokens", 0) or 0),
                int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            ),
            stop_reason=getattr(message, "stop_reason", None),
        )
