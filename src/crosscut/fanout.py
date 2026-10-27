"""Bounded fan-out for independent provider and model calls.

Research used to make its provider calls one after another: a planned pull of 33 rows and three
or more searches each, at about three seconds a search, ran for minutes with the provider idle
between requests. ``fan_out`` runs the independent calls side by side, at most ``limit`` at a
time, and returns their results in the order of the items, so a manifest built from them is the
same as the one-at-a-time loop built.

A call already started is never cancelled for a sibling's failure: a cancelled paid call is paid
again when the node replays. Every started call finishes, then the first failure (in item order)
is raised as itself. ``stop`` is checked before each call starts; once it is set no new call
starts and the item's result is ``None``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")

# provider calls in flight at once for one research step; the connectors' token buckets
# (connectors/ratelimit.py) hold each provider to its own rate whatever this is
PROVIDER_CONCURRENCY = 8


class _Raised:
    """A call's own exception, carried back so a call that *returns* an exception object is not
    mistaken for one that raised."""

    __slots__ = ("error",)

    def __init__(self, error: Exception) -> None:
        self.error = error


async def _settle(
    items: Sequence[T], call: Callable[[T], Awaitable[R | None]], limit: int
) -> list[R | None]:
    """Every call finished (none cancelled for a sibling), then the first failure raised."""
    gate = asyncio.Semaphore(max(1, limit))

    async def one(item: T) -> R | None | _Raised:
        async with gate:
            try:
                return await call(item)
            except Exception as error:  # noqa: BLE001 - re-raised below, once all calls are done
                return _Raised(error)

    settled = await asyncio.gather(*(one(i) for i in items))
    for outcome in settled:
        if isinstance(outcome, _Raised):
            raise outcome.error
    return [o for o in settled if not isinstance(o, _Raised)]


async def fan_out(
    items: Sequence[T],
    call: Callable[[T], Awaitable[R]],
    *,
    limit: int = PROVIDER_CONCURRENCY,
    stop: Callable[[], bool],
) -> list[R | None]:
    """``call(item)`` for every item, at most ``limit`` at once; results in item order.

    ``None`` stands for an item whose call never started because ``stop()`` was true. The first
    exception (by item order) is raised after every started call has finished."""

    async def guarded(item: T) -> R | None:
        """The call, unless ``stop`` was set before it started."""
        return None if stop() else await call(item)

    return await _settle(items, guarded, limit)


async def fan_out_all(
    items: Sequence[T], call: Callable[[T], Awaitable[R]], *, limit: int = PROVIDER_CONCURRENCY
) -> list[R]:
    """``fan_out`` with no ``stop``: every item runs, so every result is a ``call`` result."""
    results = await _settle(items, call, limit)
    return cast(list[R], results)
