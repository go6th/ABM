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
    # the part of the cache writes made with the one-hour lifetime (priced at 2x input)
    cache_creation_1h_input_tokens: int | None = None


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


@dataclass(frozen=True)
class BatchSlot:
    """Where one research call keeps its Message Batches request between slices (token review
    item 8, 2026-10-20): ``load``/``save`` persist ``{"batch_id", "submitted_at"}`` under the
    call's request digest, so a slice that ends while the batch is still processing leaves it
    running and the next slice re-attaches instead of paying for the call again.
    ``max_wait_seconds`` bounds how long a call waits from its first submission; past it the
    batch is cancelled (an unprocessed request is not billed) and the call is made at the
    standard price."""

    load: Any  # async () -> dict | None
    save: Any  # async (dict) -> None
    now: Any  # () -> float, wall-clock seconds (the submission time survives a restart)
    poll_seconds: float = 20.0
    max_wait_seconds: float = 1800.0
    # async () -> None: the run is alive while it waits (the stall sweeper reads this heartbeat)
    heartbeat: Any | None = None
    heartbeat_seconds: float = 300.0


@dataclass(frozen=True)
class CallOptions:
    """Request extras for one call, honoured by a port whose ``supports_call_options`` is true
    (``platform.llm.AnthropicLlm``); any other port gets ``cached_user`` folded into the user
    text and ignores the rest, so every adapter keeps its plain ``complete`` signature.

    * ``cached_user``: a large document that opens the user turn as its own cached block (the
      question follows it), so calls about the same source pay the cached-read price for it.
    * ``schema``: the reply's JSON Schema, sent as structured output so a reply cannot fail it.
    * ``batch``: send the call through the Message Batches API at half price (``BatchSlot``).
    * ``effort``: the user's effort for the role, in place of the task's."""

    cached_user: str | None = None
    schema: Mapping[str, Any] | None = None
    batch: BatchSlot | None = None
    # the effort the user chose for this call's role (medium or high, owner decision
    # 2026-10-20); None keeps the task's configured effort
    effort: str | None = None


class SecretSource(Protocol):
    """Secrets by name; values are never logged."""

    def get(self, name: str) -> str:
        """A secret by name."""
        ...
