"""Cross-run reuse of finished provider calls (``config/research_cache.yaml``).

Pull-once (ADR-0006) replays a call within one run. This module extends the replay to earlier
runs of the SAME tenant: a finished ``tool_calls`` row whose ``args_hash`` matches and whose
payload is younger than the operation's TTL is adopted by the new run. The ledger writes the new
run its own row (cost 0, ``cost_source = 'cache'``, the same ``response_ref``), so the tracker
proof, the grading lineage and the spend ledger all hold without special cases; only the provider
call and its money are saved.

* Tenant scope: every lookup runs inside ``tenant_txn``; row level security hides other tenants.
* Freshness: per-operation TTL in days; a missing operation uses ``default_ttl_days``; ``0`` or
  ``null`` disables reuse for that operation.
* Failed calls (a ``response`` whose ``error_class`` is set; successful rows carry the key with
  a null value) and rows without a payload are never reused.
* The optional index (Redis) only shortens the lookup; the Postgres row is the record. The index
  holds references and counts, never payload text or tenant names.

GUARANTEE(test_a_second_run_reuses_a_fresh_call_of_the_same_tenant_at_no_cost)
GUARANTEE(test_the_cache_never_crosses_tenants_or_a_fresh_run)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR

CACHE_COST_SOURCE = "cache"


class ModelRepliesCfg(BaseModel):
    """Reuse of model replies across runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    ttl_days: float = Field(ge=0)


class ResearchCacheConfig(BaseModel):
    """Typed view of ``config/research_cache.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    status: str
    enabled: bool
    default_ttl_days: float = Field(ge=0)
    ttl_days_by_operation: dict[str, float | None] = Field(default_factory=dict)
    model_replies: ModelRepliesCfg
    redis_ttl_s: int = Field(ge=60)

    def ttl_for(self, operation: str | None) -> timedelta | None:
        """How long a stored call of ``operation`` may be reused; None when never."""
        if not self.enabled:
            return None
        days = self.ttl_days_by_operation.get(str(operation), self.default_ttl_days)
        if days is None or days <= 0:
            return None
        return timedelta(days=float(days))


@lru_cache(maxsize=2)
def load_research_cache_config(path: Path | None = None) -> ResearchCacheConfig:
    """Load and validate the file once."""
    data = yaml.safe_load((path or CONFIG_DIR / "research_cache.yaml").read_text())
    return ResearchCacheConfig.model_validate(data)


@dataclass(frozen=True)
class CachedCall:
    """A finished call of an earlier run that the new run may adopt."""

    args_hash: str
    response: dict[str, Any] | None
    response_ref: str
    fetched_at: datetime
    source_run_id: UUID
    tool: str
    provider_code: str | None
    operation: str | None
    route: str | None


class CacheIndex(Protocol):
    """A fast key to reference index (memory or Redis). Values are small JSON objects."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """The stored object, or None."""
        ...

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        """Store an object for ``ttl_s`` seconds."""
        ...


class InMemoryCacheIndex:
    """Single-process index (tests and a worker without Redis)."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        """The stored object, or None."""
        return self.items.get(key)

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        """Store (the TTL is not enforced in memory; freshness is re-checked on read)."""
        self.items[key] = dict(value)


class RedisCacheIndex:
    """Index on a ``redis.asyncio`` client. Any Redis failure reads as a miss: the Postgres row is
    the record and a cache that is down must never fail a run."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def get(self, key: str) -> dict[str, Any] | None:
        """The stored object, or None (also on any Redis error)."""
        try:
            raw = await self.client.get(key)
        except Exception:  # noqa: BLE001 - a cache outage is a miss, never a failure
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        """Store with an expiry; errors are ignored (the row is the record)."""
        try:
            await self.client.set(key, json.dumps(value, default=str), ex=int(ttl_s))
        except Exception:  # noqa: BLE001 - see get
            return


def index_key(tenant_id: UUID, args_hash: str) -> str:
    """The index key: tenant scoped, no provider name, no account name."""
    return f"rc:v1:{tenant_id}:{args_hash}"


_LOOKUP = text(
    "SELECT run_id, response, response_ref, created_at, tool, provider_code, operation, route"
    " FROM tool_calls WHERE args_hash = :h AND response_ref IS NOT NULL"
    # a call the resumed run itself adopted counts too (resume of a resume, audit 2026-10-15)
    " AND (cost_source IS DISTINCT FROM :cache OR run_id = :src)"
    " AND (created_at >= :cutoff OR run_id = :src)"
    " AND response->>'error_class' IS NULL"
    " ORDER BY (run_id = :src) DESC, created_at DESC LIMIT 1"
)
_NEVER = datetime.max.replace(tzinfo=UTC)


class ResearchCache:
    """See the module docstring. One instance per runtime; holds config and the index only."""

    def __init__(self, config: ResearchCacheConfig, index: CacheIndex | None = None) -> None:
        self.config = config
        self.index = index or InMemoryCacheIndex()

    @property
    def enabled(self) -> bool:
        """Whether provider results may be reused at all."""
        return self.config.enabled

    async def lookup(
        self,
        s: AsyncSession,
        tenant_id: UUID,
        args_hash: str,
        operation: str | None,
        now: datetime,
        source_run: UUID | None = None,
        strict_source: bool = False,
    ) -> CachedCall | None:
        """The freshest reusable call of this tenant for ``args_hash``, or None. ``s`` must be a
        tenant transaction (row level security scopes the read). A call of ``source_run`` (the
        run this one resumes) is reused whatever its age and even when the cache is off; a
        ``started`` attempt is never a hit (only ``tool_calls`` rows exist for finished calls).
        ``strict_source`` limits the hit to ``source_run`` (a resume with the cache off)."""
        ttl = self.config.ttl_for(operation)
        if ttl is None and source_run is None:
            return None
        cutoff = now - ttl if ttl is not None and not strict_source else _NEVER
        key = index_key(tenant_id, args_hash)
        indexed = await self.index.get(key) if source_run is None else None
        if indexed is not None:
            try:
                fetched = datetime.fromisoformat(str(indexed["fetched_at"]))
                if fetched >= cutoff:
                    return CachedCall(
                        args_hash=args_hash,
                        response=indexed.get("response"),
                        response_ref=str(indexed["response_ref"]),
                        fetched_at=fetched,
                        source_run_id=UUID(str(indexed["source_run_id"])),
                        tool=str(indexed["tool"]),
                        provider_code=indexed.get("provider_code"),
                        operation=indexed.get("operation"),
                        route=indexed.get("route"),
                    )
            except (KeyError, ValueError, TypeError):
                pass  # a malformed index entry is a miss; the row below is the record
        row = (
            await s.execute(
                _LOOKUP,
                {"h": args_hash, "cache": CACHE_COST_SOURCE, "cutoff": cutoff, "src": source_run},
            )
        ).first()
        if row is None:
            return None
        hit = CachedCall(
            args_hash=args_hash,
            response=dict(row.response) if row.response is not None else None,
            response_ref=str(row.response_ref),
            fetched_at=row.created_at,
            source_run_id=UUID(str(row.run_id)),
            tool=str(row.tool),
            provider_code=row.provider_code,
            operation=row.operation,
            route=row.route,
        )
        await self.index.set(
            key,
            {
                "response": hit.response,
                "response_ref": hit.response_ref,
                "fetched_at": hit.fetched_at.isoformat(),
                "source_run_id": str(hit.source_run_id),
                "tool": hit.tool,
                "provider_code": hit.provider_code,
                "operation": hit.operation,
                "route": hit.route,
            },
            self.config.redis_ttl_s,
        )
        return hit


def redis_index(url: str) -> RedisCacheIndex:
    """A Redis index on ``url`` (``redis://host:6379/0``; ``rediss://`` for TLS)."""
    import redis.asyncio as redis

    return RedisCacheIndex(redis.Redis.from_url(url))
