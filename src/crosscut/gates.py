"""Per-research concurrency gates, one per provider, sized by the governor's own ceilings.

The governor (``parallel.governor``) holds how many calls to one provider are in flight across
every run and worker, and a caller that gets no slot within its bounded wait (``lease_wait_s``)
ends its whole slice (``SlotUnavailable``). A research that starts forty page reads at once for a
provider whose ceiling is four would put thirty-six of them in that bounded wait. ``ProviderGates``
keeps the waiting on this side, in the research's own process and without a time limit: at most
``ceiling`` of the research's calls to a provider are in the governor at any moment, so the
governor's slots are what bounds the provider and its wait is short. Nothing is shared between
researches: a ``ProviderGates`` belongs to one provider adapter of one run.

An asyncio semaphore belongs to the event loop that first used it (a slice runs on its own loop),
so the gates are built on the loop that asks for them and rebuilt when it changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def configured_ceiling(provider_code: str) -> int:
    """The provider's ceiling in ``config/parallel.yaml`` (``providers.ceilings``)."""
    from parallel.config import load_parallel_config

    return load_parallel_config().providers.ceiling(provider_code)


class ProviderGates:
    """See the module docstring. One per provider adapter instance."""

    def __init__(self, size_of: Callable[[str], int] = configured_ceiling) -> None:
        self._size_of = size_of
        self._gates: dict[str, asyncio.Semaphore] = {}
        self._loop: Any = None

    def __call__(self, provider_code: str) -> asyncio.Semaphore:
        """The gate of ``provider_code`` on the running event loop."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._gates, self._loop = {}, loop
        gate = self._gates.get(provider_code)
        if gate is None:
            gate = self._gates[provider_code] = asyncio.Semaphore(
                max(1, self._size_of(provider_code))
            )
        return gate
