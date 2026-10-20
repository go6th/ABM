"""Model reply log and cross-run reply reuse (``config/research_cache.yaml``, ``model_replies``).

Every model call of the legacy port is logged as insert-only rows of ``model_reply_log`` (tenant
scoped): a ``started`` row before the paid request (write-ahead), then how it was served
(``model``, ``run_replay`` or ``cache``) with its tokens and its priced cost. A row served by the
model also keeps the reply text, with the run id replaced by a placeholder, so a later run of the
same tenant whose request is byte-identical (after the same normalisation) gets the same text back
without a model call and with zero usage.

Why the normalisation: payload references carry the run id
(``tenants/<t>/accounts/<a>/runs/<run>/raw/<sha>``) and the content hash is stable, so the only
difference between two runs' identical requests is the run id in those references. The reply
cites the same references, so the placeholder is substituted back on a hit.

No prompt is stored: the key is a hash of the request; only the reply text is kept, and only for
replies that validated at the call site (an empty reply is never stored).

GUARANTEE(test_a_reply_is_reused_across_runs_with_the_run_id_rewritten)
GUARANTEE(test_reply_reuse_expires_and_stays_within_the_tenant)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text

RUN_PLACEHOLDER = "{run}"
ServedFrom = str  # "started" | "model" | "run_replay" | "cache"


def normalise(body: str, run_id: UUID) -> str:
    """The text with this run's id replaced by the placeholder."""
    return body.replace(str(run_id), RUN_PLACEHOLDER)


def denormalise(body: str, run_id: UUID) -> str:
    """Inverse of ``normalise`` for the run that reads the reply."""
    return body.replace(RUN_PLACEHOLDER, str(run_id))


def reply_key(task_kind: str, tier: str, max_tokens: int, system: str, user: str) -> str:
    """Deterministic key of one request (``user`` already normalised)."""
    body = json.dumps([task_kind, tier, max_tokens, system, user], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class CachedReply:
    """A stored reply of an earlier run."""

    text: str  # normalised (placeholder for the run id)
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


@dataclass(frozen=True)
class ReplyUsage:
    """Token counts of one call as the provider reported them (zero for a reuse)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # the part of ``cache_write_tokens`` written with the one-hour lifetime (billed at twice the
    # input price instead of the five-minute write price)
    cache_write_1h_tokens: int = 0
    # served through the Message Batches API (every token at half price)
    batch: bool = False


# the Message Batches discount on every token class (platform.claude.com pricing, 2026-10-20)
BATCH_DISCOUNT = Decimal("0.5")
# a one-hour cache write costs twice the base input price
CACHE_WRITE_1H_MULTIPLIER = Decimal(2)


class ReplyCache(Protocol):
    """Log every call; look up a reusable reply of this tenant."""

    async def get(
        self, tenant_id: UUID, key: str, now: datetime, *, source_run: UUID | None = None
    ) -> CachedReply | None:
        """The freshest stored reply for ``key`` within the TTL, or None; a reply of
        ``source_run`` (the run this one resumes) is reused whatever its age."""
        ...

    async def record(
        self,
        tenant_id: UUID,
        run_id: UUID,
        key: str,
        task_kind: str,
        tier: str,
        model: str,
        served_from: ServedFrom,
        usage: ReplyUsage,
        cost_usd: Decimal | None,
        reply_text: str | None,
    ) -> None:
        """One insert-only log row (``reply_text`` only for rows served by the model)."""
        ...


class InMemoryReplyCache:
    """Single-process store for tests."""

    def __init__(self, ttl_days: float = 7.0) -> None:
        self.ttl = timedelta(days=ttl_days)
        self.rows: list[dict[str, Any]] = []

    async def get(
        self, tenant_id: UUID, key: str, now: datetime, *, source_run: UUID | None = None
    ) -> CachedReply | None:
        """See ``ReplyCache.get``."""
        candidates = [
            r
            for r in self.rows
            if r["tenant_id"] == tenant_id
            and r["key"] == key
            # a reused reply of the run this one resumes counts too (resume of a resume)
            and (r["served_from"] == "model" or r["run_id"] == source_run)
            and r["reply_text"]
        ]
        candidates.sort(key=lambda r: (r["run_id"] == source_run, r["created_at"]))
        for row in reversed(candidates):
            if row["run_id"] == source_run or row["created_at"] >= now - self.ttl:
                return CachedReply(
                    row["reply_text"],
                    row["model"],
                    row["usage"].input_tokens,
                    row["usage"].output_tokens,
                    row["created_at"],
                )
        return None

    async def record(
        self,
        tenant_id: UUID,
        run_id: UUID,
        key: str,
        task_kind: str,
        tier: str,
        model: str,
        served_from: ServedFrom,
        usage: ReplyUsage,
        cost_usd: Decimal | None,
        reply_text: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        """See ``ReplyCache.record``."""
        from datetime import UTC

        self.rows.append(
            {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "key": key,
                "task_kind": task_kind,
                "tier": tier,
                "model": model,
                "served_from": served_from,
                "usage": usage,
                "cost_usd": cost_usd,
                "reply_text": reply_text,
                "created_at": now or datetime.now(UTC),
            }
        )


class PgReplyCache:
    """``model_reply_log`` under the tenant transaction (migration 0049)."""

    def __init__(self, txn: Any, ttl_days: float) -> None:
        self.txn = txn
        self.ttl = timedelta(days=ttl_days)

    async def get(
        self, tenant_id: UUID, key: str, now: datetime, *, source_run: UUID | None = None
    ) -> CachedReply | None:
        """See ``ReplyCache.get``."""
        async with self.txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT reply_text, reply_model, input_tokens, output_tokens, created_at"
                        " FROM model_reply_log WHERE cache_key = :k"
                        # a reply the resumed run itself reused counts too, so a resume of a
                        # resume never pays for it again (audit 2026-10-15)
                        " AND (served_from = 'model' OR run_id = :src)"
                        " AND reply_text IS NOT NULL"
                        " AND (created_at >= :cutoff OR run_id = :src)"
                        " ORDER BY (run_id = :src) DESC, created_at DESC LIMIT 1"
                    ),
                    {"k": key, "cutoff": now - self.ttl, "src": source_run},
                )
            ).first()
        if row is None:
            return None
        return CachedReply(
            str(row.reply_text),
            str(row.reply_model or ""),
            int(row.input_tokens),
            int(row.output_tokens),
            row.created_at,
        )

    async def record(
        self,
        tenant_id: UUID,
        run_id: UUID,
        key: str,
        task_kind: str,
        tier: str,
        model: str,
        served_from: ServedFrom,
        usage: ReplyUsage,
        cost_usd: Decimal | None,
        reply_text: str | None,
    ) -> None:
        """See ``ReplyCache.record``."""
        async with self.txn(tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier,"
                    " model, served_from, input_tokens, output_tokens, cache_read_tokens,"
                    " cache_write_tokens, cost_usd, cost_known, reply_text, reply_model, batch)"
                    " VALUES ((SELECT app_tenant()), :r, :k, :task, :tier, :model, :served,"
                    " :i, :o, :cr, :cw, :cost, :known, :reply, :reply_model, :batch)"
                ),
                {
                    "r": run_id,
                    "k": key,
                    "task": task_kind,
                    "tier": tier,
                    "model": model,
                    "served": served_from,
                    "i": usage.input_tokens,
                    "o": usage.output_tokens,
                    "cr": usage.cache_read_tokens,
                    "cw": usage.cache_write_tokens,
                    "cost": cost_usd,
                    "known": cost_usd is not None,
                    "reply": reply_text,
                    "reply_model": model if reply_text is not None else None,
                    "batch": usage.batch,
                },
            )


@lru_cache(maxsize=1)
def _prices(path: Path | None = None) -> dict[str, dict[str, Decimal]]:
    """Per-model prices (USD per million tokens) from ``config/models.yaml``; read once."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    doc = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    out: dict[str, dict[str, Decimal]] = {}
    for model, spec in (doc.get("models") or {}).items():
        pricing = spec.get("pricing") or {}
        out[str(model)] = {k: Decimal(str(v)) for k, v in pricing.items()}
    return out


def price(model: str, usage: ReplyUsage) -> Decimal | None:
    """The call's cost from ``config/models.yaml`` prices (uncached input, cached reads, cache
    writes and output priced separately; one-hour cache writes at twice the input price; a
    batched call at half price); None when the model is not priced there."""
    p = _prices().get(model)
    if p is None:
        return None
    million = Decimal(1_000_000)
    base_input = p.get("input_usd_per_million", Decimal(0))
    long_writes = min(usage.cache_write_1h_tokens, usage.cache_write_tokens)
    cost = (
        Decimal(usage.input_tokens) * base_input
        + Decimal(usage.cache_read_tokens) * p.get("cached_read_usd_per_million", Decimal(0))
        + Decimal(usage.cache_write_tokens - long_writes)
        * p.get("cache_write_usd_per_million", Decimal(0))
        + Decimal(long_writes) * base_input * CACHE_WRITE_1H_MULTIPLIER
        + Decimal(usage.output_tokens) * p.get("output_usd_per_million", Decimal(0))
    ) / million
    return cost * BATCH_DISCOUNT if usage.batch else cost
