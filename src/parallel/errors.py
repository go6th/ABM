"""Typed failures of parallel execution. Every refusal has its own type (no bare errors).

GUARANTEE(test_dag_rejects_a_cycle): a cyclic dependency graph is ``DagInvalid``.
"""

from __future__ import annotations


class ParallelError(RuntimeError):
    """Base class."""


class ConfigInvalid(ParallelError):
    """``config/parallel.yaml`` is invalid or an entry has no source or status."""


class DagInvalid(ParallelError):
    """The dependency graph is cyclic, references an unknown unit type or is not covered."""


class UnitNotAllowed(ParallelError):
    """A unit outside the configured allow-list (for example one a model suggested, P3)."""


class FanOutCapExceeded(ParallelError):
    """A dynamic fan-out or the run's unit total would exceed its hard cap (P3)."""


class SchedulerDeadlock(ParallelError):
    """Nothing is ready, nothing is in flight and work is pending (4.2 ``no_progress``)."""

    def __init__(self, pending: tuple[str, ...]) -> None:
        super().__init__(f"{len(pending)} units can never become ready")
        self.pending = pending


class LeaseWaitTimeout(ParallelError):
    """A provider slot was not granted within the bounded lease wait; the unit is re-queued with
    its age preserved (queueing, never a provider retry)."""

    def __init__(self, provider_code: str, waited_s: float) -> None:
        super().__init__(f"no {provider_code} slot within {waited_s:.1f}s")
        self.provider_code = provider_code
        self.waited_s = waited_s


class LeaseLost(ParallelError):
    """A heartbeat found the lease expired or reclaimed: the holder must stop using the slot."""


class MergeConflict(ParallelError):
    """Two units claimed the same output key with different content (merge is by unit key)."""


class Cancelled(ParallelError):
    """The cooperative cancellation token fired (slice deadline, run cancel or pause)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
