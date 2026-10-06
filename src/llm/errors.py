"""Typed outcomes of a model call (R4: which classes fall back, which never do).

* ``Overloaded`` (429, 529, 5xx, timeouts) is a ``RetryableProviderError``: the node RetryPolicy
  owns the retry; the router may select the next candidate for the next attempt.
* ``ModelUnavailable`` and ``ContextTooLong`` fall back by selection on the next attempt.
* ``SchemaInvalid``, ``AuthFailed`` and ``ContentRefused`` never fall back.

GUARANTEE(test_fallback_happens_only_on_the_allowed_classes): R4.
"""

from __future__ import annotations

from account_intelligence.connectors.errors import RetryableProviderError


class LlmError(RuntimeError):
    """Base class of routing and model errors (messages carry no prompt or document text)."""


class ConfigInvalid(LlmError):
    """``config/model_routing.yaml`` is invalid or unsourced."""


class UnknownTaskKind(LlmError):
    """A call site named no task kind or one not in config (R2)."""


class NoEligibleCandidate(LlmError):
    """No candidate of the tier may serve this call (data class, independence, health, context)."""


class DataClassRefused(NoEligibleCandidate):
    """Tenant or private data would reach a candidate without verified zero retention (R8)."""


class JudgeNotIndependent(NoEligibleCandidate):
    """The judge's family equals the generator's (R6): the G12 cap path applies."""


class BudgetRefused(LlmError):
    """The run's token cap cannot cover this call (R9); refused before the call."""


class SchemaInvalid(LlmError):
    """The response failed its schema after the bounded re-ask (R3). Never a fallback."""


class AuthFailed(LlmError):
    """Credentials or permission refused: page the operator; never retried, never a fallback."""


class ContentRefused(LlmError):
    """The model refused on content grounds: an outcome, never a fallback."""


class ModelUnavailable(LlmError):
    """The model is unknown or retired at the provider: the next attempt selects another."""


class ContextTooLong(LlmError):
    """The input is longer than the candidate's context: the next attempt selects a larger one."""


class Overloaded(RetryableProviderError):
    """429, overload, 5xx or a timeout: the node RetryPolicy retries (W6)."""

    reason = "model temporarily unavailable"


FALLBACK_CLASSES = frozenset({"Overloaded", "ModelUnavailable", "ContextTooLong"})
NEVER_FALLBACK = frozenset({"SchemaInvalid", "AuthFailed", "ContentRefused"})
