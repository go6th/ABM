"""Value types of a routed model call (no prompt or document text is ever logged from these)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Usage:
    """Token counts as the provider reported them (cache read and write recorded, R10)."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def uncached_input_tokens(self) -> int:
        """Input tokens neither read from nor written to the cache."""
        return max(0, self.input_tokens - self.cache_read_tokens - self.cache_write_tokens)


@dataclass(frozen=True)
class Scope:
    """Who the call is for. ``generator_family`` is set for judge-tier calls (R6)."""

    tenant_id: UUID
    run_id: UUID
    data_class: str = "public_web"
    depth: str = "standard"
    generator_family: str | None = None


@dataclass(frozen=True)
class AdapterReply:
    """What an adapter returns: raw text, the model id it reports, and usage."""

    text: str
    model: str
    usage: Usage
    stop_reason: str | None = None


@dataclass(frozen=True)
class Attempt:
    """A failed attempt of one call key (the router's selection history)."""

    candidate: str
    error_class: str


@dataclass(frozen=True)
class CallRecord:
    """One stored call (insert-only); replayed by call key at no cost (R9)."""

    call_key: str
    task_kind: str
    tier: str
    candidate: str
    model_alias: str
    provider_code: str
    family: str
    routing_version: str
    prompt_hash: str
    degraded: bool
    degraded_reason: str | None
    usage: Usage
    cost_usd: Decimal | None
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Routed:
    """A validated output and its record."""

    output: Any
    record: CallRecord
    replayed: bool
