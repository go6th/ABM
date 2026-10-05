"""Broker connector layer (work package 1.4). Public API only.

The research graph calls ``call_operation`` (one provider operation) or ``call_need``
(a routed research need with fallback). Everything else is internal.
"""

from account_intelligence.connectors.base import (
    CallContext,
    ConnectorResult,
    Counts,
    Operation,
    ProviderCode,
)
from account_intelligence.connectors.errors import (
    AuthError,
    BudgetExceeded,
    CircuitOpen,
    ConnectorError,
    CreditsExhausted,
    LeaseUnavailable,
    NonRetryableProviderError,
    NotSupportedYet,
    PaginationLoopDetected,
    PolicyRefused,
    RateLimited,
    RetryableProviderError,
    SchemaDriftError,
    SecurityError,
)
from account_intelligence.connectors.router import call_need
from account_intelligence.connectors.service import (
    ConnectorRuntime,
    call_operation,
    retry_policy_for,
)

__all__ = [
    "AuthError",
    "BudgetExceeded",
    "CallContext",
    "CircuitOpen",
    "ConnectorError",
    "ConnectorResult",
    "ConnectorRuntime",
    "Counts",
    "CreditsExhausted",
    "LeaseUnavailable",
    "NonRetryableProviderError",
    "NotSupportedYet",
    "Operation",
    "PaginationLoopDetected",
    "PolicyRefused",
    "ProviderCode",
    "RateLimited",
    "RetryableProviderError",
    "SchemaDriftError",
    "SecurityError",
    "call_need",
    "call_operation",
    "retry_policy_for",
]
