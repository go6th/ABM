"""Single flight: identical concurrent operations in one worker share one execution (P8).

Keyed by the operation's ledger hash (provider, operation and arguments). The first caller runs
the operation; concurrent callers of the same key await the same future and receive its result or
its exception. The entry is dropped when the operation ends, so a later caller runs again and is
answered by the ledger's pull-once replay at zero cost. Across workers the ledger stays the
authority (an identical claim waits for the in-flight attempt, then replays). Cancelling one
waiter never cancels the shared execution.

GUARANTEE(test_single_flight_runs_concurrent_identical_calls_once): P8 in one worker.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class InProcessSingleFlight:
    """The ``SingleFlight`` port for one worker process."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self.shared = 0  # callers served by another caller's execution (observability)

    async def do(self, key: str, run: Callable[[], Awaitable[Any]]) -> Any:
        """See the module docstring."""
        existing = self._inflight.get(key)
        if existing is not None:
            self.shared += 1
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._inflight[key] = future
        try:
            result = await run()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark retrieved: waiters re-raise it, nobody else must
            raise
        else:
            future.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)
