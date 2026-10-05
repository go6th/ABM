"""In-memory fakes for the export builders (spec 1.2, 9). Synthetic only; no network, no clock.

* ``FakeReader``: returns a given ``SnapshotContent`` (or raises a given error).
* ``MemoryStore``: ``DeliverableStore`` in memory with the same contract as the Postgres store:
  put-if-absent objects (a different body under an existing key raises), append-only rows, the next
  version per account and kind with ``supersedes``, a repeated set returns the stored rows.
  ``fail_after_objects`` simulates a kill between the object puts and the row commit.
* ``FixedClock``: a constant time (only ``built_at`` on records ever reads it).
* ``InProcessRenderer``: the interim renderer called in process (fast property tests); the
  subprocess renderer is used everywhere else.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from exports.content import SnapshotContent
from exports.errors import ExportError
from exports.models import Deliverable
from exports.ports import StoredFile
from exports.store import ObjectConflict, object_key
from exports.word.contract import DocumentSpec
from exports.word.render_cli import render


class FakeReader:
    """``ContentReader`` returning fixed content."""

    def __init__(self, content: SnapshotContent | None = None, error: ExportError | None = None):
        self.content = content
        self.error = error
        self.calls = 0

    async def read(self, tenant_id: str, run_id: str) -> SnapshotContent:
        """The content (or the configured error)."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.content is not None
        return self.content


class FixedClock:
    """A constant, timezone-aware time."""

    def __init__(self, at: datetime | None = None) -> None:
        self.at = at or datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """The fixed time."""
        return self.at


class KilledMidBuild(RuntimeError):
    """Raised by ``MemoryStore`` to simulate a process killed before the row commit."""


@dataclass
class MemoryStore:
    """``DeliverableStore`` in memory (see the module docstring)."""

    clock: FixedClock = field(default_factory=FixedClock)
    objects: dict[str, bytes] = field(default_factory=dict)
    rows: list[tuple[str, str, str, str, Deliverable, dict[str, object]]] = field(
        default_factory=list
    )  # (tenant, account, run, set_hash, deliverable, manifest)
    fail_after_objects: bool = False
    puts: int = 0

    async def find_set(self, tenant_id: str, run_id: str, set_hash: str) -> tuple[Deliverable, ...]:
        """Rows of the set."""
        return tuple(
            sorted(
                (d for t, _, r, h, d, _ in self.rows if (t, r, h) == (tenant_id, run_id, set_hash)),
                key=lambda d: d.kind,
            )
        )

    async def previous_manifest(
        self, tenant_id: str, account_id: str, run_id: str
    ) -> dict[str, object] | None:
        """Manifest of the account's latest workspace."""
        found = [
            (d.version, m)
            for t, a, _, _, d, m in self.rows
            if t == tenant_id and a == account_id and d.kind == "workspace"
        ]
        return max(found, key=lambda x: x[0])[1] if found else None

    async def put_set(
        self,
        tenant_id: str,
        account_id: str,
        run_id: str,
        set_hash: str,
        files: tuple[StoredFile, ...],
        manifest: dict[str, object],
        snapshot_hash: str,
        theme_hash: str,
    ) -> tuple[Deliverable, ...]:
        """Store the set (objects put-if-absent, then all rows at once)."""
        existing = await self.find_set(tenant_id, run_id, set_hash)
        if existing:
            return existing
        staged = []
        for f in files:
            sha = hashlib.sha256(f.body).hexdigest()
            key = object_key(tenant_id, account_id, run_id, f.kind, sha)
            if key in self.objects and self.objects[key] != f.body:
                raise ObjectConflict("object key exists with different bytes", (key,))
            if key not in self.objects:
                self.objects[key] = f.body
                self.puts += 1
            staged.append((f, sha, key))
        if self.fail_after_objects:
            raise KilledMidBuild("killed before the row commit")
        new = []
        for f, sha, key in staged:
            prev = [
                d
                for t, a, _, _, d, _ in self.rows + new
                if t == tenant_id and a == account_id and d.kind == f.kind
            ]
            last = max(prev, key=lambda d: d.version) if prev else None
            d = Deliverable(
                id=str(uuid.uuid4()),
                kind=f.kind,
                version=(last.version + 1) if last else 1,
                supersedes=last.id if last else None,
                filename=f.filename,
                sha256=sha,
                size=len(f.body),
                object_key=key,
                content_hash=f.content_hash,
                snapshot_hash=snapshot_hash,
                theme_hash=theme_hash,
                built_at=self.clock.now(),
            )
            new.append(
                (
                    tenant_id,
                    account_id,
                    run_id,
                    set_hash,
                    d,
                    json.loads(json.dumps(manifest, default=str)),
                )
            )
        self.rows.extend(new)
        return tuple(sorted((x[4] for x in new), key=lambda d: d.kind))


class InProcessRenderer:
    """``WordRenderPort`` calling the interim renderer in process (no subprocess)."""

    def __init__(self) -> None:
        self.last_exit = "not_run"
        self.calls = 0

    def render(self, spec: DocumentSpec) -> bytes:
        """docx bytes."""
        self.calls += 1
        self.last_exit = "converged"
        return render(DocumentSpec.model_validate_json(spec.model_dump_json()))
