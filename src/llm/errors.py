"""Typed outcomes of a model call (provider errors mapped by ``llm.adapters``).

* ``Overloaded`` (429, 529, 5xx, timeouts) is a ``RetryableProviderError``: the node RetryPolicy
  owns the retry (W6).
* ``SchemaInvalid``, ``AuthFailed``, ``ContentRefused``, ``ProviderCreditsExhausted``,
  ``ModelUnavailable`` and ``ContextTooLong`` are outcomes the graph maps to a stop reason; none
  is retried on another model (the one model fallback is a Kimi K3 or Grok 4.5 step that cannot
  meet its schema, ``agents.production.wiring.RunDepsModel``).

The routing errors (``UnknownTaskKind``, ``JudgeNotIndependent``) live in ``llm.route``.
"""

from __future__ import annotations

from account_intelligence.connectors.errors import RetryableProviderError


class LlmError(RuntimeError):
    """Base class of model errors (messages carry no prompt or document text)."""


class SchemaInvalid(LlmError):
    """The response failed its schema after the bounded re-ask (R3)."""


class AuthFailed(LlmError):
    """Credentials or permission refused: page the operator; never retried."""


class ContentRefused(LlmError):
    """The model refused on content grounds: an outcome, never retried."""


class ProviderCreditsExhausted(LlmError):
    """The model provider refuses calls until credits are bought: page the operator; never
    retried. The run keeps what it pulled and ends partial."""


class ModelUnavailable(LlmError):
    """The model is unknown or retired at the provider."""


class ContextTooLong(LlmError):
    """The input is longer than the model's context."""


class Overloaded(RetryableProviderError):
    """429, overload, 5xx or a timeout: the node RetryPolicy retries (W6)."""

    reason = "model temporarily unavailable"
