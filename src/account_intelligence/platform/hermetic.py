"""Hermetic adapters: in-process scheduler, memory payloads, environment secrets, a scripted LLM.

Allowed in ``dev`` and ``test`` only; ``app.container.assert_gcp_runtime`` refuses them in
staging and production (W4).
"""

from __future__ import annotations

import asyncio
import gzip
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from account_intelligence.platform.ports import LlmResult, LlmUsage, NotConfigured


class SystemClock:
    """Wall clock, aware UTC (the only adapter that reads it)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class MemoryPayloadStore:
    """Payloads in a dict; one assignment per put (atomic)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes) -> str:
        self.objects[key] = gzip.compress(body, mtime=0)
        return key

    async def get(self, key: str) -> bytes:
        return gzip.decompress(self.objects[key])


class LocalPayloadStore:
    """Filesystem store for development and multi-process tests.

    Atomic: the object is written to a temporary file in the same directory, flushed,
    then renamed over the final name, so a reader never sees a partial payload.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents:
            raise ValueError("payload key escapes the store root")
        return path

    async def put(self, key: str, body: bytes) -> str:
        await asyncio.to_thread(self._write, self._path(key), gzip.compress(body, mtime=0))
        return key

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def get(self, key: str) -> bytes:
        data = await asyncio.to_thread(self._path(key).read_bytes)
        return gzip.decompress(data)


SliceRunner = Callable[[UUID, UUID, int], Awaitable[None]]


@dataclass
class InProcessScheduler:
    """Records tasks; ``drain`` runs queued slices in order (tests and local dev). Same
    idempotency contract as Cloud Tasks named tasks: one task per (run, slice)."""

    runner: SliceRunner | None = None
    queued: list[tuple[UUID, UUID, int]] = field(default_factory=list)
    seen: set[tuple[UUID, int]] = field(default_factory=set)
    timeouts: dict[UUID, datetime] = field(default_factory=dict)

    async def enqueue_slice(
        self, tenant_id: UUID, run_id: UUID, slice_no: int, delay_s: float = 0.0
    ) -> None:
        if (run_id, slice_no) in self.seen:
            return
        self.seen.add((run_id, slice_no))
        self.queued.append((tenant_id, run_id, slice_no))

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        self.timeouts.setdefault(run_id, at)

    async def drain(self, limit: int = 1000) -> int:
        """Run queued slices (a slice may enqueue the next). Bounded by ``limit``."""
        if self.runner is None:
            raise NotConfigured("in-process scheduler has no slice runner")
        done = 0
        while self.queued and done < limit:
            tenant, run, slice_no = self.queued.pop(0)
            await self.runner(tenant, run, slice_no)
            done += 1
        return done


class EnvSecrets:
    """Secrets from the environment (dev only)."""

    def get(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise NotConfigured(f"secret {name} is not set")
        return value


Script = Callable[[str, str, str], str]


@dataclass
class ScriptedLlm:
    """Deterministic LLM for tests: ``script(tier, system, user) -> text``."""

    script: Script
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        self.calls.append((tier, user))
        text = self.script(tier, system, user)
        return LlmResult(text, f"scripted-{tier}", LlmUsage(len(user) // 4, len(text) // 4))


class UnconfiguredLlm:
    """The LLM port when no provider is configured: every call raises ``NotConfigured``."""

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        raise NotConfigured("no LLM provider is configured")
