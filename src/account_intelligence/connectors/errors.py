"""Typed failure classes for the broker connector layer.

One owner per failure class (skill reference 04): the connector classifies, the
LangGraph node ``RetryPolicy`` decides whether to retry (``RetryableProviderError``
only), and the router decides whether to fall back. Messages never carry a vendor
name, a secret or untrusted page text; ``blocked_reason`` is the neutral text that
reaches a coverage row.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class. ``reason`` is neutral and safe for an operator-facing coverage row."""

    reason = "provider call failed"

    def __init__(
        self,
        message: str = "",
        *,
        provider_code: str | None = None,
        operation: str | None = None,
        http_status: int | None = None,
        attempt: int | None = None,
    ) -> None:
        super().__init__(message or self.reason)
        self.provider_code = provider_code
        self.operation = operation
        self.http_status = http_status
        # Ledger attempt number for the (run, args) key; set by call_operation once the
        # intent row exists. The graph node compares it with its RetryPolicy budget.
        self.attempt = attempt
        # Ledger key (args_hash) of the attempt that failed; set by call_operation so the stage
        # can cite the failed call as tracker proof (T2, T6) without inventing a reference.
        self.call_ref: str | None = None

    @property
    def blocked_reason(self) -> str:
        """Neutral reason text for a Blocked coverage row (provider code and operation only)."""
        where = f"{self.provider_code}.{self.operation}" if self.provider_code else "provider"
        return f"{where}: {self.reason}"


class RetryableProviderError(ConnectorError):
    """429, 5xx, timeouts, connection resets. Only the node RetryPolicy retries these."""

    reason = "provider temporarily unavailable"


class RateLimited(RetryableProviderError):
    """429 or Retry-After: the shared cooldown is set, then the node retries."""

    reason = "provider rate limit reached"

    def __init__(
        self, message: str = "", *, retry_after: float | None = None, **kw: object
    ) -> None:
        super().__init__(message, **kw)  # type: ignore[arg-type]
        self.retry_after = retry_after


class NonRetryableProviderError(ConnectorError):
    """400, 404 for a valid resource, provider-side validation. Never retried."""

    reason = "provider rejected the request"


class ResponseTooLarge(NonRetryableProviderError):
    """The body passed the size cap while streaming."""

    reason = "provider response exceeded the size cap"


class UnexpectedContentType(NonRetryableProviderError):
    """The response content type is not the documented one."""

    reason = "provider returned an unexpected content type"


class AuthError(ConnectorError):
    """401/403 that mean credentials or plan. Page the operator; never retry."""

    reason = "provider credentials rejected"


class CreditsExhausted(ConnectorError):
    """The provider says the balance or quota is empty. Router may fall back."""

    reason = "provider balance or quota exhausted"


class SchemaDriftError(ConnectorError):
    """The response no longer matches the sourced schema. Never repaired, never retried."""

    reason = "provider response did not match the expected schema"

    def __init__(self, message: str = "", *, raw_ref: str | None = None, **kw: object) -> None:
        super().__init__(message, **kw)  # type: ignore[arg-type]
        self.raw_ref = raw_ref


class BudgetExceeded(ConnectorError):
    """The run's dollar cap or call cap would be exceeded. Run ends BUDGET_EXCEEDED."""

    reason = "run budget or call cap reached"


class CircuitOpen(ConnectorError):
    """The provider's breaker is open; routing may fall back."""

    reason = "provider circuit open after repeated failures"


class SecurityError(ConnectorError):
    """SSRF, allow-list, policy or secret-handling refusal. Never retried, never fallen back."""

    reason = "request refused by security policy"


class PolicyRefused(SecurityError):
    """An adapter rule from the routing playbook refused the call."""

    reason = "request refused by adapter rule"


class NotSupportedYet(ConnectorError):
    """The operation's provider spec is not sourced; see docs/providers/<code>.md."""

    reason = "operation not available: provider contract not yet verified"


class PaginationLoopDetected(ConnectorError):
    """A provider repeated a cursor or a page; the pull ends partial."""

    reason = "provider pagination repeated a cursor or page"


class LeaseUnavailable(ConnectorError):
    """Another worker holds the run lease; yield and let the orchestrator reschedule."""

    reason = "another worker is executing this run"


class LeaseLost(LeaseUnavailable):
    """Another worker took the run lease; this worker must stop paying."""

    reason = "this worker no longer holds the run lease"


class CallInFlight(RetryableProviderError):
    """An identical call by this worker is still running past the bounded wait."""

    reason = "identical call still in flight"


class ProviderJobTimeout(RetryableProviderError):
    """An asynchronous provider job did not finish within its deadline."""

    reason = "provider job did not finish within its deadline"


# Classes after which the router may try the fallback provider (section 3.6).
FALLBACK_ON: tuple[type[ConnectorError], ...] = (CreditsExhausted, CircuitOpen)
# Never fall back on these: a different provider would hide drift or bypass a refusal.
NEVER_FALL_BACK: tuple[type[ConnectorError], ...] = (SchemaDriftError, SecurityError)
