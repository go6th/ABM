"""Chunked, resumable uploads (never a whole file in memory).

``init`` records the declared name and size and returns the part size; ``put_part`` streams one
part's request body straight to object storage and records its size and hash (a part may be sent
again: it replaces the earlier one); ``state`` lists the parts received, so a client resumes by
sending only the missing ones; ``complete`` composes the parts into one object while hashing it,
checks the size, and either links an existing file with the same SHA-256 (dedupe: nothing stored
or processed twice) or creates the file row in UPLOADED. The raw file is stored before anything
parses it.

GUARANTEE(test_upload_resumes_by_missing_parts_and_dedupes_by_sha256)
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from research_files import repo
from research_files.blobstore import BlobStore, PartTooLarge, file_key, part_key, upload_prefix
from research_files.config import ResearchFilesConfig, load_config
from research_files.detect import detect

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


class UploadError(ValueError):
    """A refused upload step; ``code`` is stable, ``status`` the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Completed:
    """The file a completed upload produced (or the existing file with the same content)."""

    file_id: UUID
    status: str
    deduplicated: bool


def clean_name(name: str) -> str:
    """A display name: the last path part, control characters removed, at most 255 characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable()).strip()
    return (base or "file")[:255]


@dataclass
class Uploads:
    """See the module docstring."""

    blobs: BlobStore
    txn: TxnFactory
    cfg: ResearchFilesConfig | None = None

    @property
    def conf(self) -> ResearchFilesConfig:
        """The config (loaded on first use)."""
        return self.cfg or load_config()

    async def init(
        self, tenant_id: UUID, user: str, name: str, size: int, mime: str | None
    ) -> dict[str, Any]:
        """Open an upload."""
        limit = self.conf.upload.max_file_bytes
        if size < 0 or size > limit:
            raise UploadError("file_too_large", f"Files up to {limit} bytes are accepted.", 413)
        part = self.conf.upload.part_bytes
        total = max(1, math.ceil(size / part))
        async with self.txn(tenant_id) as s:
            upload_id = await repo.create_upload(
                s,
                name=clean_name(name),
                size=size,
                mime=(mime or None) and mime[:120],
                part_size=part,
                parts_total=total,
                user=user,
                expires_at=datetime.now(UTC) + timedelta(hours=self.conf.upload.upload_ttl_hours),
            )
        return {"upload_id": str(upload_id), "part_size": part, "parts_total": total}

    async def _open(self, s: AsyncSession, upload_id: UUID) -> Any:
        row = await repo.get_upload(s, upload_id)
        if row is None:
            raise UploadError("upload_not_found", "Upload not found.", 404)
        if row.status != "open":
            raise UploadError("upload_closed", "This upload is already complete.", 409)
        if row.expires_at < datetime.now(UTC):
            raise UploadError("upload_expired", "This upload expired; start a new one.", 410)
        return row

    async def put_part(
        self, tenant_id: UUID, upload_id: UUID, part_no: int, body: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        """Stream one part to storage; its size must be the expected one."""
        async with self.txn(tenant_id) as s:
            row = await self._open(s, upload_id)
        if not 0 <= part_no < row.parts_total:
            raise UploadError("part_out_of_range", "Part number out of range.")
        expected = (
            row.size_bytes - row.part_size * (row.parts_total - 1)
            if part_no == row.parts_total - 1
            else row.part_size
        )
        key = part_key(tenant_id, upload_id, part_no)
        try:
            size, digest = await self.blobs.write_stream(key, body, max_bytes=expected)
        except PartTooLarge:
            await self.blobs.delete(key)
            raise UploadError(
                "part_size_mismatch", f"Part {part_no} must be {expected} bytes."
            ) from None
        if size != expected:
            await self.blobs.delete(key)
            raise UploadError("part_size_mismatch", f"Part {part_no} must be {expected} bytes.")
        async with self.txn(tenant_id) as s:
            await repo.record_part(s, upload_id, part_no, size, digest)
        return {"part": part_no, "size": size, "sha256": digest}

    async def state(self, tenant_id: UUID, upload_id: UUID) -> dict[str, Any]:
        """Which parts have arrived."""
        async with self.txn(tenant_id) as s:
            row = await repo.get_upload(s, upload_id)
            if row is None:
                raise UploadError("upload_not_found", "Upload not found.", 404)
            got = await repo.parts(s, upload_id)
        received = [int(p.part_no) for p in got]
        return {
            "upload_id": str(upload_id),
            "status": row.status,
            "parts_total": row.parts_total,
            "part_size": row.part_size,
            "received": received,
            "missing": [n for n in range(row.parts_total) if n not in set(received)],
            "file_id": str(row.file_id) if row.file_id else None,
        }

    async def complete(self, tenant_id: UUID, upload_id: UUID, user: str) -> Completed:
        """Compose, hash, dedupe, create the file row (see the module docstring)."""
        async with self.txn(tenant_id) as s:
            row = await repo.get_upload(s, upload_id)
            if row is not None and row.status == "completed" and row.file_id:
                existing = await repo.get_file(s, row.file_id)
                if existing is not None:
                    return Completed(UUID(str(row.file_id)), existing.status, True)
            row = await self._open(s, upload_id)
            got = await repo.parts(s, upload_id)
        if [int(p.part_no) for p in got] != list(range(row.parts_total)):
            raise UploadError("parts_missing", "Some parts have not been uploaded yet.", 409)
        assembled = upload_prefix(tenant_id, upload_id) + "assembled"
        size, digest = await self.blobs.compose(
            assembled, [part_key(tenant_id, upload_id, n) for n in range(row.parts_total)]
        )
        if size != row.size_bytes:
            await self.blobs.delete(assembled)
            raise UploadError("size_mismatch", "The parts do not add up to the declared size.")
        async with self.txn(tenant_id) as s:
            existing = await repo.file_by_sha(s, digest)
            if existing is not None:
                await repo.finish_upload(s, upload_id, UUID(str(existing.id)))
        if existing is not None:
            await self.blobs.delete_prefix(upload_prefix(tenant_id, upload_id))
            return Completed(UUID(str(existing.id)), existing.status, True)
        file_id = uuid4()
        final = file_key(tenant_id, file_id)
        await self.blobs.move(assembled, final)
        with self.blobs.open(final) as handle:
            kind = detect(handle, row.name)
        async with self.txn(tenant_id) as s:
            created = await repo.insert_file(
                s,
                file_id=file_id,
                name=row.name,
                size=size,
                mime=row.mime,
                detected=kind,
                sha=digest,
                key=final,
                user=user,
            )
            if created:
                await repo.finish_upload(s, upload_id, file_id)
            else:
                existing = await repo.file_by_sha(s, digest)
                await repo.finish_upload(s, upload_id, UUID(str(existing.id)))
        await self.blobs.delete_prefix(upload_prefix(tenant_id, upload_id))
        if not created:  # a concurrent upload of the same bytes won the insert
            await self.blobs.delete(final)
            return Completed(UUID(str(existing.id)), existing.status, True)
        return Completed(file_id, "UPLOADED", False)
