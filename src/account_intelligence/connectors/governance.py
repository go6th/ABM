"""Ports the connector calls around every provider exchange (D-43, P4, P8, P9).

* ``ProviderGovernor``: how many calls to one provider are in flight across all runs and workers
  (``parallel.governor``). ``call_operation`` takes a slot only for a call that is not already in
  the ledger (a replay never needs one) and only for a live provider exchange (a pre-pulled file
  needs none); the slot is given back in ``finally``. A slot not granted within the bounded wait
  raises ``SlotUnavailable`` **before** the ledger claim, so queueing never consumes a ledger
  attempt; the executor re-queues the unit with its age preserved. The governor never retries.
* ``SingleFlight``: identical concurrent operations inside one worker share one execution; across
  workers the ledger's in-flight wait and pull-once replay stay the authority.
* ``UNIT_PRIORITY`` and ``CANCEL_CHECK``: context variables the parallel executor sets for each
  unit task (priority for fair slot order; the cooperative cancellation check run before each
  call). Unset outside parallel execution (priority 0, no check).

Signals: a ``RateLimited`` response reports ``rate_limited``; a clean response reports ``clean``.

GUARANTEE(test_call_operation_holds_a_slot_only_around_live_calls): connector integration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from typing import Any, Literal, Protocol

from account_intelligence.connectors.base import CallContext

Signal = Literal["rate_limited", "clean"]

UNIT_PRIORITY: ContextVar[int] = ContextVar("unit_priority", default=0)
CANCEL_CHECK: ContextVar[Callable[[], None] | None] = ContextVar("cancel_check", default=None)


class SlotUnavailable(RuntimeError):  # noqa: N818 - matches the port's vocabulary
    """No provider slot was granted within the bounded wait. Deliberately NOT a
    ``ConnectorError``: stage code must not turn queueing into a failed call or a Blocked row."""

    def __init__(self, provider_code: str, waited_s: float) -> None:
        super().__init__(f"no {provider_code} slot within {waited_s:.1f}s")
        self.provider_code = provider_code
        self.waited_s = waited_s


class ProviderGovernor(Protocol):
    """Per-provider concurrency across all runs and workers."""

    def slot(
        self, ctx: CallContext, provider_code: str, priority: int
    ) -> AbstractAsyncContextManager[float]:
        """Hold one slot for the body of the ``async with`` (yields the seconds waited)."""
        ...

    async def signal(self, ctx: CallContext, provider_code: str, kind: Signal) -> None:
        """Report a rate-limit signal or a clean response (AIMD input)."""
        ...


class SingleFlight(Protocol):
    """Coalesce identical concurrent operations (keyed by the ledger's operation hash)."""

    async def do(self, key: str, run: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``run`` once for concurrent callers of ``key``; all callers get its outcome."""
        ...
