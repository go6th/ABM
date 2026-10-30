"""``DeliverableStore`` on PostgreSQL and the payload store (B14, spec 8.4).

* Objects are content-addressed and neutral:
  ``<tenant>/<account>/<run>/deliverables/<sha256>.<ext>`` (no provider or broker name, no
  account name). ``put_if_absent`` writes an object only when the key is new, and refuses a key
  whose stored bytes differ (it can never overwrite).
* A set is stored in ONE tenant transaction: every file of the set gets a row, the next version
  for its account and kind, and ``supersedes`` the previous version. A repeated build of the same
  content (same run and set hash) returns the stored rows and writes nothing.
* A kill before the commit leaves no row, so no deliverable is ever marked stored for a partial set;
  objects already put are content-addressed and are reused by the retry (an unreferenced object is
  never served; a sweeper for them is a listed follow-up).
* Only INSERT and SELECT are granted on ``deliverables``; nothing is updated or deleted.
GUARANTEE(test_repeat_build_writes_nothing_and_redelivery_supersedes)
GUARANTEE(test_a_kill_mid_build_leaves_nothing_complete)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.platform.ports import PayloadStore
from exports.errors import ExportError
from exports.models import Deliverable
from exports.ports import Clock, StoredFile

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_EXT = {
    "summary": "docx",
    "evidence": "docx",
    "people": "xlsx",
    "jobs": "xlsx",
    "tech": "xlsx",
    "workspace": "zip",
}
_COLS = (
    "id, kind, version, supersedes, filename, sha256, size_bytes, object_key, content_hash,"
    " snapshot_hash, theme_hash, built_at"
)
_LATEST = (
    "SELECT id, version FROM deliverables WHERE account_id = :a AND kind = :k"
    " ORDER BY version DESC LIMIT 1"
)
_INSERT = (
    "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version, supersedes,"  # nosec B608 -- fixed INSERT and return columns.
    " filename, sha256, size_bytes, object_key, manifest_json, content_hash, snapshot_hash,"
    " theme_hash, rules_hash, built_at) VALUES (:t, :a, :r, :h, :k, :v, :sup, :fn, :sha, :size,"
    " :key, CAST(:m AS jsonb), :ch, :sh, :th, :rh, :at) RETURNING " + _COLS
)


class ObjectConflict(ExportError):
    """An object key exists with different bytes: storage never overwrites."""

    code = "object_conflict"


def object_key(tenant_id: str, account_id: str, run_id: str, kind: str, sha256: str) -> str:
    """The neutral, content-addressed key of one file."""
    return f"{tenant_id}/{account_id}/{run_id}/deliverables/{sha256}.{_EXT[kind]}"


def _missing() -> tuple[type[BaseException], ...]:
    """Exceptions the payload stores raise for a missing key (memory, local, Cloud Storage)."""
    errors: list[type[BaseException]] = [KeyError, FileNotFoundError]
    try:
        from google.api_core.exceptions import NotFound
    except ImportError:  # pragma: no cover - the dependency is in the image
        return tuple(errors)
    errors.append(NotFound)
    return tuple(errors)


async def put_if_absent(payloads: PayloadStore, key: str, body: bytes) -> bool:
    """Write ``body`` under ``key`` unless it exists; True when written. A different existing
    body raises ``ObjectConflict`` (never overwritten)."""
    try:
        existing: bytes | None = await payloads.get(key)
    except _missing():
        existing = None
    if existing is not None:
        if hashlib.sha256(existing).digest() != hashlib.sha256(body).digest():
            raise ObjectConflict("object key exists with different bytes", (key,))
        return False
    await payloads.put(key, body)
    return True


def _row(r: Any) -> Deliverable:
    return Deliverable(
        id=str(r.id),
        kind=r.kind,
        version=r.version,
        supersedes=str(r.supersedes) if r.supersedes else None,
        filename=r.filename,
        sha256=r.sha256,
        size=r.size_bytes,
        object_key=r.object_key,
        content_hash=r.content_hash,
        snapshot_hash=r.snapshot_hash,
        theme_hash=r.theme_hash,
        built_at=r.built_at,
    )


class PgDeliverableStore:
    """``DeliverableStore`` (see the module docstring)."""

    def __init__(
        self, txn: TxnFactory, payloads: PayloadStore, clock: Clock, rules_hash: str
    ) -> None:
        self.txn = txn
        self.payloads = payloads
        self.clock = clock
        self.rules_hash = rules_hash

    async def find_set(self, tenant_id: str, run_id: str, set_hash: str) -> tuple[Deliverable, ...]:
        """The stored rows of this set, in kind order (empty when not stored)."""
        async with self.txn(UUID(tenant_id)) as s:
            rows = (
                await s.execute(
                    text(
                        f"SELECT {_COLS} FROM deliverables WHERE run_id = :r AND set_hash = :h"  # nosec B608 -- fixed return columns.
                        " ORDER BY kind"
                    ),
                    {"r": UUID(run_id), "h": set_hash},
                )
            ).all()
        return tuple(_row(r) for r in rows)

    async def previous_manifest(
        self, tenant_id: str, account_id: str, run_id: str
    ) -> dict[str, object] | None:
        """The manifest stored with the account's latest workspace of ANOTHER run: the change note
        compares this run with the previous research, so a rebuild of the run (a fixed bug, an
        exclusion added later) is never compared with its own earlier set (leakage review
        2026-10-30, F7)."""
        async with self.txn(UUID(tenant_id)) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT manifest_json FROM deliverables WHERE account_id = :a AND kind ="
                        " 'workspace' AND run_id <> :r ORDER BY version DESC LIMIT 1"
                    ),
                    {"a": UUID(account_id), "r": UUID(run_id)},
                )
            ).first()
        return dict(row.manifest_json) if row else None

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
        """Store the set (see the module docstring)."""
        existing = await self.find_set(tenant_id, run_id, set_hash)
        if existing:
            return existing
        keyed = []
        for f in files:
            sha = hashlib.sha256(f.body).hexdigest()
            key = object_key(tenant_id, account_id, run_id, f.kind, sha)
            await put_if_absent(self.payloads, key, f.body)
            keyed.append((f, sha, key))
        built_at = self.clock.now()
        manifest_json = json.dumps(manifest, sort_keys=True, default=str)
        out: list[Deliverable] = []
        async with self.txn(UUID(tenant_id)) as s:
            # serialise versioning per account (concurrent builds of one account queue here)
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"deliverables:{account_id}"},
            )
            again: int = (
                await s.execute(
                    text("SELECT count(*) FROM deliverables WHERE run_id = :r AND set_hash = :h"),
                    {"r": UUID(run_id), "h": set_hash},
                )
            ).scalar_one()
            if again:
                return await self.find_set(tenant_id, run_id, set_hash)
            for f, sha, key in keyed:
                prev = (
                    await s.execute(
                        text(_LATEST),
                        {"a": UUID(account_id), "k": f.kind},
                    )
                ).first()
                row = (
                    await s.execute(
                        text(_INSERT),
                        {
                            "t": UUID(tenant_id),
                            "a": UUID(account_id),
                            "r": UUID(run_id),
                            "h": set_hash,
                            "k": f.kind,
                            "v": (prev.version + 1) if prev else 1,
                            "sup": prev.id if prev else None,
                            "fn": f.filename,
                            "sha": sha,
                            "size": len(f.body),
                            "key": key,
                            "m": manifest_json
                            if f.kind == "workspace"
                            else json.dumps(
                                {
                                    "rows": f.rows,
                                    "content_hash": f.content_hash,
                                    "exclusion_hash": manifest.get("exclusion_hash", ""),
                                    # the files the set does not hold (``kind:rule``), on every
                                    # row: the gate and the file listing read it from any file
                                    "files_withheld": manifest.get("files_withheld", []),
                                }
                            ),
                            "ch": f.content_hash,
                            "sh": snapshot_hash,
                            "th": theme_hash,
                            "rh": self.rules_hash,
                            "at": built_at,
                        },
                    )
                ).one()
                out.append(_row(row))
        return tuple(sorted(out, key=lambda d: d.kind))
