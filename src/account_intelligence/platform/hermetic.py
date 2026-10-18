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

from account_intelligence.platform.ports import (
    LlmResult,
    LlmUsage,
    NotConfigured,
    PayloadMissing,
)


class SystemClock:
    """Wall clock, aware UTC (the only adapter that reads it)."""

    def now(self) -> datetime:
        """Current aware UTC time."""
        return datetime.now(UTC)


class MemoryPayloadStore:
    """Payloads in a dict; one assignment per put (atomic)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes) -> str:
        """Store the gzipped body in one assignment."""
        self.objects[key] = gzip.compress(body, mtime=0)
        return key

    async def get(self, key: str) -> bytes:
        """Decompress a stored body (``PayloadMissing`` when absent)."""
        try:
            return gzip.decompress(self.objects[key])
        except KeyError:
            raise PayloadMissing(key) from None


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
        """Atomic write: temporary file, fsync, rename."""
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
        """Read and decompress a stored body (``PayloadMissing`` when absent)."""
        try:
            data = await asyncio.to_thread(self._path(key).read_bytes)
        except FileNotFoundError:
            raise PayloadMissing(key) from None
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
        """Queue a slice once per (run, slice)."""
        if (run_id, slice_no) in self.seen:
            return
        self.seen.add((run_id, slice_no))
        self.queued.append((tenant_id, run_id, slice_no))

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        """Arm the run's approval timeout once."""
        self.timeouts.setdefault(run_id, at)

    async def drain(self, limit: int = 1000) -> int:
        """Run queued slices (a slice may enqueue the next). Bounded by ``limit``."""
        if self.runner is None:
            raise NotConfigured("in-process scheduler has no slice runner")
        done = 0
        # LOOP(in-process slice drain, tests and dev): cap=limit (1000); exit=queue empty, limit
        while self.queued and done < limit:
            tenant, run, slice_no = self.queued.pop(0)
            await self.runner(tenant, run, slice_no)
            done += 1
        return done


class HttpTaskScheduler:
    """Local stack scheduler: posts slice tasks to the worker service over HTTP, the same body
    Cloud Tasks delivers (``/tasks/run-slice``; the local worker accepts it only with
    ``ALLOW_UNAUTHENTICATED_TASKS=1``). Fire-and-forget like a queue; idempotent per slice."""

    def __init__(self, worker_url: str) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.seen: set[tuple[UUID, int]] = set()
        self.tasks: set[asyncio.Task[None]] = set()

    async def _post(self, path: str, body: dict[str, object], delay_s: float) -> None:
        import httpx

        from account_intelligence.platform.request_context import REQUEST_ID

        if delay_s:
            await asyncio.sleep(delay_s)
        # a slice runs up to 240 s (1200 s on the uncapped local stack): a bounded read well above
        # it, short connect/write/pool
        timeout = httpx.Timeout(connect=5.0, read=1800.0, write=30.0, pool=5.0)
        request_id = REQUEST_ID.get()
        headers = {"X-Request-ID": request_id} if request_id else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(f"{self.worker_url}{path}", json=body, headers=headers)

    def _spawn(self, path: str, body: dict[str, object], delay_s: float = 0.0) -> None:
        task = asyncio.get_running_loop().create_task(self._post(path, body, delay_s))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def enqueue_slice(
        self, tenant_id: UUID, run_id: UUID, slice_no: int, delay_s: float = 0.0
    ) -> None:
        """Post the slice once."""
        if (run_id, slice_no) in self.seen:
            return
        self.seen.add((run_id, slice_no))
        body = {"tenant_id": str(tenant_id), "run_id": str(run_id), "slice": slice_no}
        self._spawn("/tasks/run-slice", body, delay_s)

    async def schedule_timeout(self, tenant_id: UUID, run_id: UUID, at: datetime) -> None:
        """Post the approval-timeout task at its deadline."""
        delay = max(0.0, (at - datetime.now(UTC)).total_seconds())
        self._spawn(
            "/tasks/approval-timeout", {"tenant_id": str(tenant_id), "run_id": str(run_id)}, delay
        )


class EnvSecrets:
    """Secrets from the environment (dev only)."""

    def get(self, name: str) -> str:
        """A secret from the environment, or NotConfigured."""
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
        """Answer from the script and record the call."""
        self.calls.append((tier, user))
        text = self.script(tier, system, user)
        return LlmResult(text, f"scripted-{tier}", LlmUsage(len(user) // 4, len(text) // 4))


class UnconfiguredLlm:
    """The LLM port when no provider is configured: every call raises ``NotConfigured``."""

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """Always NotConfigured (no provider is configured)."""
        raise NotConfigured("no LLM provider is configured")
