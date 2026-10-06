"""Model call records (R9): every call is one insert-only row keyed by ``(run, call_key)`` and
replayed from it at no cost on resume; failed attempts are insert-only rows the router reads as
its selection history. Stores: ``InMemoryCallStore`` (tests) and ``PgCallStore``
(``llm_calls`` and ``llm_call_attempts``, migration 0018). No prompt or document text is stored:
only the validated output map, counts, cost and identifiers.

GUARANTEE(test_a_resumed_call_replays_and_never_pays_twice): R9.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm.models import Attempt, CallRecord, Usage


def call_key(
    task_kind: str, prompt_hash: str, payload: Any, schema_name: str, routing_version: str
) -> str:
    """Deterministic key of one logical call."""
    body = json.dumps(
        [task_kind, prompt_hash, payload, schema_name, routing_version],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


class CallStore(Protocol):
    """Insert-only call records and failed attempts of one run."""

    async def get(self, key: str) -> CallRecord | None:
        """The stored call, or None."""
        ...

    async def attempts(self, key: str) -> list[Attempt]:
        """Failed attempts of this call key, in order."""
        ...

    async def tokens_used(self) -> int:
        """Input plus output tokens of the run's stored calls."""
        ...

    async def add_attempt(self, key: str, attempt: Attempt) -> None:
        """Record a failed attempt."""
        ...

    async def put(self, record: CallRecord) -> CallRecord:
        """Insert; a concurrent duplicate returns the stored row (first write wins)."""
        ...


class InMemoryCallStore:
    """One-process store."""

    def __init__(self) -> None:
        self.calls: dict[str, CallRecord] = {}
        self.failed: dict[str, list[Attempt]] = {}

    async def get(self, key: str) -> CallRecord | None:
        """The stored call, or None."""
        return self.calls.get(key)

    async def attempts(self, key: str) -> list[Attempt]:
        """Failed attempts of this call key."""
        return list(self.failed.get(key, []))

    async def tokens_used(self) -> int:
        """Tokens of the stored calls."""
        return sum(r.usage.input_tokens + r.usage.output_tokens for r in self.calls.values())

    async def add_attempt(self, key: str, attempt: Attempt) -> None:
        """Record a failed attempt."""
        self.failed.setdefault(key, []).append(attempt)

    async def put(self, record: CallRecord) -> CallRecord:
        """Insert (first write wins)."""
        return self.calls.setdefault(record.call_key, record)


class PgCallStore:
    """``llm_calls`` and ``llm_call_attempts`` for one run (tenant from the run, never input)."""

    def __init__(
        self,
        txn: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
        tenant_id: UUID,
        run_id: UUID,
    ) -> None:
        self.txn, self.tenant_id, self.run_id = txn, tenant_id, run_id

    async def get(self, key: str) -> CallRecord | None:
        """The stored call, or None."""
        async with self.txn(self.tenant_id) as s:
            r = (
                await s.execute(
                    text(
                        "SELECT call_key, task_kind, tier, candidate, model_alias, provider_code,"
                        " family, routing_version, prompt_hash, degraded, degraded_reason,"
                        " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
                        " cost_usd, output_json FROM llm_calls WHERE run_id = :r AND call_key = :k"
                    ),
                    {"r": self.run_id, "k": key},
                )
            ).first()
        if r is None:
            return None
        return CallRecord(
            r.call_key,
            r.task_kind,
            r.tier,
            r.candidate,
            r.model_alias,
            r.provider_code,
            r.family,
            r.routing_version,
            r.prompt_hash,
            bool(r.degraded),
            r.degraded_reason,
            Usage(r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens),
            None if r.cost_usd is None else Decimal(r.cost_usd),
            dict(r.output_json),
        )

    async def attempts(self, key: str) -> list[Attempt]:
        """Failed attempts of this call key."""
        async with self.txn(self.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT candidate, error_class FROM llm_call_attempts WHERE run_id = :r"
                        " AND call_key = :k ORDER BY id"
                    ),
                    {"r": self.run_id, "k": key},
                )
            ).all()
        return [Attempt(str(r.candidate), str(r.error_class)) for r in rows]

    async def tokens_used(self) -> int:
        """Tokens of the run's stored calls."""
        async with self.txn(self.tenant_id) as s:
            value: Any = (
                await s.execute(
                    text(
                        "SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM llm_calls"
                        " WHERE run_id = :r"
                    ),
                    {"r": self.run_id},
                )
            ).scalar_one()
        return int(value)

    async def add_attempt(self, key: str, attempt: Attempt) -> None:
        """Record a failed attempt."""
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO llm_call_attempts (run_id, call_key, candidate, error_class)"
                    " VALUES (:r, :k, :c, :e)"
                ),
                {"r": self.run_id, "k": key, "c": attempt.candidate, "e": attempt.error_class},
            )

    async def put(self, record: CallRecord) -> CallRecord:
        """Insert (first write wins on the unique key)."""
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO llm_calls (run_id, call_key, task_kind, tier, candidate,"
                    " model_alias, provider_code, family, routing_version, prompt_hash, degraded,"
                    " degraded_reason, input_tokens, output_tokens, cache_read_tokens,"
                    " cache_write_tokens, cost_usd, cost_known, output_json) VALUES (:r, :k, :tk,"
                    " :t, :c, :m, :p, :f, :rv, :ph, :d, :dr, :it, :ot, :cr, :cw, :cost, :known,"
                    " CAST(:o AS jsonb)) ON CONFLICT (run_id, call_key) DO NOTHING"
                ),
                {
                    "r": self.run_id,
                    "k": record.call_key,
                    "tk": record.task_kind,
                    "t": record.tier,
                    "c": record.candidate,
                    "m": record.model_alias,
                    "p": record.provider_code,
                    "f": record.family,
                    "rv": record.routing_version,
                    "ph": record.prompt_hash,
                    "d": record.degraded,
                    "dr": record.degraded_reason,
                    "it": record.usage.input_tokens,
                    "ot": record.usage.output_tokens,
                    "cr": record.usage.cache_read_tokens,
                    "cw": record.usage.cache_write_tokens,
                    "cost": record.cost_usd,
                    "known": record.cost_usd is not None,
                    "o": json.dumps(record.output, sort_keys=True, default=str),
                },
            )
        stored = await self.get(record.call_key)
        assert stored is not None
        return stored
