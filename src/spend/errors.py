"""Typed failures of the credit cap (every refusal has its own type)."""

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class SpendError(RuntimeError):
    """Base class."""


class CapNotApproved(SpendError):
    """No approved cap on a run that requires one: no paid call (K2)."""


class CapExhausted(SpendError):
    """The approved cap cannot cover this call; carries what remains."""

    def __init__(self, message: str, remaining_micros: int = 0) -> None:
        super().__init__(message)
        self.remaining_micros = remaining_micros


class ProposalStale(SpendError):
    """The proposal or its balance snapshot changed or expired before approval."""


class ApprovalInvalid(SpendError):
    """An approval that is not bound, not human, expired, replayed or from another tenant."""


class UnknownCostRefused(SpendError):
    """An operation without a configured hold and no approved sample line (K5)."""


class ExtensionDenied(SpendError):
    """An extension that is not allowed (unattended, over the limit, not higher, not approved)."""


class BalanceUnknown(SpendError):
    """A provider balance could not be read; never inferred."""


class ReconciliationFailed(SpendError):
    """Derived spend does not equal the run's counters or the ledger (K7)."""


class ConfigError(SpendError):
    """config/spend.yaml is invalid or unsourced."""


class NotConfigured(SpendError, PlatformNotConfigured):
    """A production adapter for a spend port does not exist."""
