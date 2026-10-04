"""Typed boundaries of the credit cap. A production adapter that does not exist raises
``NotConfigured``: no balance endpoint is sourced, so balances read ``unknown`` (never inferred)
and the operator prompt goes nowhere until a notifier exists. Fakes: ``tests/fakes/spend.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from spend.models import BalanceSnapshot, PlanItem


class BalancePort(Protocol):
    """Free balance check per provider (PRD step 1: the check is free)."""

    async def balance(self, provider_code: str) -> BalanceSnapshot:
        """The provider's balance now, with its source and status."""
        ...


class PlanPort(Protocol):
    """The pending paid operations of the run with their row priority."""

    async def pending(self) -> Sequence[PlanItem]:
        """Pending plan items."""
        ...


class Notifier(Protocol):
    """Prompts the operator (approval or extension waiting). Carries identifiers only."""

    async def prompt(self, run_id: str, kind: str, version: int) -> None:
        """Send one prompt."""
        ...


class TrackerPort(Protocol):
    """Marks rows left unfunded or exhausted, with the reason, through the tracker."""

    async def mark(self, row_keys: Sequence[str], reason: str) -> None:
        """Record the rows with the reason (the tracker derives the status)."""
        ...


class CapPolicyPort(Protocol):
    """Hook for the per-tenant daily cap and monthly refresh budget (D-29: not built here)."""

    async def allows(self, cap_micros: int) -> bool:
        """Whether the tenant's policy allows a cap of this size."""
        ...
