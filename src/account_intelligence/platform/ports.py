"""The canonical platform ports (audit F-6: one ``PayloadStore``, one ``Clock``, one
``NotConfigured``; older module-level copies re-export these)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


class PayloadMissing(KeyError):
    """A payload key has no object. Every ``PayloadStore`` raises exactly this for a missing key
    (memory, local file and Cloud Storage alike), so callers handle one typed error."""


class NotConfigured(RuntimeError):
    """A required production adapter is missing. A stage that meets it ends its rows Not done
    with ``not_available_yet`` and the run PARTIAL with a reason, never a fake success."""


class Clock(Protocol):
    """Injected time; pure modules never read the wall clock."""

    def now(self) -> datetime:
        """Current aware UTC time."""
        ...


class PayloadStore(Protocol):
    """Raw payloads under neutral keys (no provider name in any key). One atomic put."""

    async def put(self, key: str, body: bytes) -> str:
        """Store a body under a neutral key; returns the key."""
        ...

    async def get(self, key: str) -> bytes:
        """The stored body (an error when missing, never empty bytes)."""
        ...


class RunScheduler(Protocol):
    """Starts the next slice of a run and arms the approval timeout. Events carry ids only.

    ``enqueue_slice`` is idempotent per ``(run_id, slice_no)``: enqueuing the same slice twice
    starts it once. ``schedule_timeout`` arms one approval-timeout task per run."""

    async def enqueue_slice(
        self, tenant_id: UUID, run_id: UUID, slice_no: int, delay_s: float = 0.0
    ) -> None:
        """Start a run's slice once, optionally after a delay."""
        ...

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        """Arm the run's approval-timeout task once."""
        ...


@dataclass(frozen=True)
class LlmUsage:
    """Token counts as the provider reported them (cache read and write recorded, spec 7.7)."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


@dataclass(frozen=True)
class LlmResult:
    """The raw text the model returned and its usage. Callers validate the text with a pydantic
    schema; a model never sets a grade, a status, a count or an identity."""

    text: str
    model: str
    usage: LlmUsage
    extra: Mapping[str, Any] = field(default_factory=dict)


class LlmPort(Protocol):
    """One model call. ``tier`` (planner, extractor, judge...) resolves to a model in config;
    no model id appears in code."""

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """One model call for a tier: system and user text in, raw text and usage out."""
        ...


class SecretSource(Protocol):
    """Secrets by name; values are never logged."""

    def get(self, name: str) -> str:
        """A secret by name."""
        ...
