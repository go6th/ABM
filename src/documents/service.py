"""Uploaded documents from upload to index (input mode "file"), at any size.

::

    start_upload -> chunks straight to object storage (a resumable session; the API's
                    token-checked content route only where the store has none)
      -> complete (size checked; status quarantined; processing enqueued)
      -> process: type check from content -> malware scan -> sha256 -> promote out of quarantine
         into the raw file store (0078: one object per tenant and sha256 — a re-upload of the
         same bytes registers no second copy; a changed file is a new hash, so a superseded
         version is kept until its own document is deleted)
         -> plan the parts (in the sandbox) -> read each part in its own sandbox child:
            company evidence (raw) -> redact -> chunk -> passages, written in batches
         -> (bound to an account) embed and index in the private ``chunks`` index, page by page

* Files of any size (owner decision 2026-10-14, Phase 4B section 3): a file is split into parts
  (``documents.parts``) stored in ``document_parts`` with a status each. Workers take parts
  through a lease, at most ``max_parallel_parts`` per client at once; the rest wait. A part
  writes passages numbered from its own range (part n: n * 10^7 + 1, ...), and a part read again
  after a crash first removes what it wrote, so no passage is ever stored twice. A part that
  fails keeps its reason, the other parts go on, and "Try again" queues it again
  (``retry_part``). Every step renews its lease while it runs. Nothing holds the whole file, all
  its passages, or all its vectors in memory.

* Nothing is parsed before the scan passed; a wrong type, an oversized file, an infected file or
  an unavailable scanner ends ``rejected`` with the reason, and the parser never sees the bytes.
* Audio is accepted only when a transcription vendor is configured; today it is rejected with a
  clear message.
* A document uploaded without an account stops at ``parsed`` with the detected company; the
  user confirms the company (``confirm_company``), which binds and indexes it. Ambiguity is never
  resolved by guessing.
* Deleting a document removes its index rows, passages, redaction map and row, and marks every
  fact that cites it for re-evaluation; its raw object and registry row go only when no other
  document of the tenant shares the same bytes (brief B1).
* Every step holds a lease with an expiry: a worker that dies is retried by the sweeper.

GUARANTEE(test_file_upload_runs_through_scan_parse_and_index)
GUARANTEE(test_delete_removes_chunks_and_marks_facts_for_reevaluation)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import secrets
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from documents import repo
from documents.chunking import Chunker, Passage, TableIngestTruncated
from documents.company import Resolver
from documents.config import InputModesConfig, load_config
from documents.embed import EmbeddingPort
from documents.redact import Redactor
from documents.sandbox import ParsedUnit, SandboxFailed, parse_in_sandbox, plan_in_sandbox
from documents.scan import ScanPort
from documents.shapes import ShapeLog
from obs.logging import logger
from research_files.blobstore import BlobMissing, BlobStore, PartTooLarge
from research_files.detect import by_name, detect

_log = logger(__name__)
TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_NAME = re.compile(r"[^\w .()\-]+")
ACTIVE = ("quarantined", "scanning", "parsing", "indexing")
MESSAGES = {
    "too_large": "The file is larger than the per-file limit.",
    "wrong_type": "The file type is not supported, or its content does not match its name.",
    "audio_not_supported": (
        "Audio transcription is not set up for this workspace, so audio files cannot be"
        " researched yet. Upload a transcript instead."
    ),
    "infected": "The malware scan found a threat; the file was removed and never opened.",
    "scan_unavailable": (
        "The malware scan could not run, so the file was not opened. Try again later."
    ),
    "size_mismatch": "The uploaded size does not match the declared size. Upload it again.",
    "not_uploaded": "No file arrived for this upload. Upload it again.",
    "empty": "The file has no readable content.",
    "parts_failed": "Some parts of the file could not be read. Try those parts again.",
}
# archive members the parser listed but did not read (the file list says so, by name)
SKIP_CHECKS = {
    "unsupported_member": "not a document type",
    "zip_bomb_blocked": "expands far beyond its declared size",
    "path_traversal_blocked": "unsafe file name",
    "corrupt_member": "cannot be read",
    "link_or_device_skipped": "a link or device, not a file",
    "archive_too_many_members": "past the member limit",
    "archive_too_deep": "an archive nested too deep",
    "archive_too_large": "past the uncompressed size limit",
}
PART_MESSAGES = {
    "parse:parse_timeout": "This part took too long to read.",
    "parse:text_limit": "This part holds more text than one part may.",
    "parse:page_limit": "This part has more pages than one part may.",
    "parse:unit_too_large": "One row or line in this part is too large to read.",
    "parse:unsupported_type": "This part's content could not be read.",
    "parse:table_truncated": (
        "A table in this part has more rows than one part may hold, so it was not stored in"
        " part: split the file and upload it again."
    ),
}


def part_message(failure: str | None) -> str:
    """The plain reason a part failed."""
    return PART_MESSAGES.get(failure or "", "This part could not be read.")


class DocumentError(Exception):
    """A request the document service refuses; ``code`` names why (HTTP status by the route)."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or MESSAGES.get(code, code))
        self.code = code


def clean_name(name: str) -> str:
    """A display name without path parts or control characters (never used in a key)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    out = _NAME.sub("_", base).strip(" .") or "document"
    return out[:255]


def quarantine_key(tenant_id: UUID, document_id: UUID) -> str:
    """Where uploaded bytes wait for the scan (separate from accepted documents), under the
    top-level ``quarantine/`` prefix so the bucket's lifecycle rule deletes what is never
    completed. Documents created before this keep the key stored in their row."""
    return f"quarantine/tenants/{tenant_id}/{document_id}/raw"


def document_key(tenant_id: UUID, document_id: UUID) -> str:
    """Where a scanned document lived before 0078 (the raw file store); still the answer for
    documents read before the registry existed."""
    return f"tenants/{tenant_id}/documents/{document_id}/raw"


def raw_key(tenant_id: UUID, sha256: str) -> str:
    """Where raw bytes live, content-addressed: one object per (tenant, sha256)."""
    return f"tenants/{tenant_id}/raw/{sha256}"


@dataclass
class DocumentService:
    """See the module docstring."""

    txn: TxnFactory
    blobs: BlobStore
    scanner: ScanPort
    embedder: EmbeddingPort
    scheduler: Any
    clock: Any
    cfg: InputModesConfig | None = None
    worker: str = ""
    # parts one call of ``read_parts`` reads before it hands the rest to a new task (None: all).
    # The cloud sets 1, so one Cloud Tasks request never runs past the 30-minute maximum.
    parts_per_request: int | None = None
    # seconds one call of ``_index`` embeds before it hands the rest to a new task (None: all).
    # The cloud sets 600: one request never runs into the Cloud Tasks or Cloud Run limits.
    index_seconds_per_request: float | None = None
    # retrieval vectors (documents.embed.FastEmbedder, on-server); None indexes without them
    semantic: Any | None = None

    def __post_init__(self) -> None:
        self.cfg = self.cfg or load_config()
        self.worker = self.worker or f"docs-{uuid4().hex[:8]}"

    @property
    def c(self) -> InputModesConfig:
        """The loaded configuration."""
        assert self.cfg is not None
        return self.cfg

    # ------------------------------------------------------------------ upload

    def declared_type(self, name: str) -> str:
        """The type a name declares; ``DocumentError`` when it is not accepted."""
        kind = by_name(name)
        if kind in self.c.upload.audio_types:
            raise DocumentError("audio_not_supported")
        if kind is None or kind not in self.c.upload.document_types:
            raise DocumentError("wrong_type")
        return kind

    async def max_file_bytes(self, s: AsyncSession) -> int:
        """The per-file limit: the admin's platform setting (the config value without one)."""
        return await repo.platform_max_file_bytes(s) or self.c.upload.max_file_bytes

    async def start_upload(
        self,
        tenant_id: UUID,
        user: str,
        name: str,
        size: int,
        mime: str | None,
        account_id: UUID | None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """A document row and where to send its bytes (checked before any byte arrives): a
        resumable session straight to object storage, in ``chunk_bytes`` chunks."""
        display = clean_name(name)
        kind = self.declared_type(display)
        if size <= 0:
            raise DocumentError("empty")
        doc_id = uuid4()
        token = secrets.token_urlsafe(32)
        expires = self.clock.now() + timedelta(minutes=self.c.upload.upload_ttl_minutes)
        qkey = quarantine_key(tenant_id, doc_id)
        async with self.txn(tenant_id) as s:
            if size > await self.max_file_bytes(s):
                raise DocumentError("too_large")
            if account_id is not None:
                from sqlalchemy import text

                found = (
                    await s.execute(text("SELECT 1 FROM accounts WHERE id = :a"), {"a": account_id})
                ).first()
                if found is None:
                    raise DocumentError("account_not_found", "The account was not found.")
            await repo.create(
                s,
                document_id=doc_id,
                account_id=account_id,
                name=display,
                declared_bytes=size,
                declared_type=kind,
                quarantine_key=qkey,
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                uploaded_by=user,
                expires_at=expires,
            )
        content_type = mime or "application/octet-stream"
        session = await self.blobs.resumable_upload(qkey, size, content_type, origin)
        url = session or f"/v1/documents/{doc_id}/content?token={token}"
        return {
            "document_id": str(doc_id),
            "upload_url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_at": expires.isoformat(),
            "max_bytes": size,
            "direct": session is not None,
            "protocol": "resumable" if session is not None else "single",
            "chunk_bytes": self.c.upload.chunk_bytes,
        }

    async def receive(
        self, tenant_id: UUID, document_id: UUID, token: str, chunks: AsyncIterator[bytes]
    ) -> int:
        """The local stand-in for a signed URL: the token must match, the upload must be open and
        unexpired, and at most the declared size is accepted (into quarantine only)."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id)
        if row is None:
            raise DocumentError("not_found", "The document was not found.")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if (
            row.status != "awaiting_upload"
            or row.upload_token_sha256 is None
            or not secrets.compare_digest(digest, row.upload_token_sha256)
        ):
            raise DocumentError("upload_closed", "This upload target is not valid.")
        if row.upload_expires_at < self.clock.now():
            raise DocumentError("upload_expired", "This upload target has expired.")
        try:
            size, _ = await self.blobs.write_stream(
                row.quarantine_key, chunks, max_bytes=int(row.declared_bytes)
            )
        except PartTooLarge:
            raise DocumentError("size_mismatch") from None
        return size

    async def file_limit(self, tenant_id: UUID) -> int:
        """The per-file limit for this client (what a server-side fetch may download)."""
        async with self.txn(tenant_id) as s:
            return await self.max_file_bytes(s)

    async def import_file(
        self,
        tenant_id: UUID,
        user: str,
        name: str,
        path: Path,
        mime: str | None,
        account_id: UUID | None,
    ) -> dict[str, Any]:
        """A file the server fetched for the user (a Google Drive import): declared like an
        upload (name, type, size and the per-file limit checked first), streamed from ``path``
        into quarantine, then completed, so the scan, typing from content and reading are
        exactly an upload's. The caller deletes ``path``."""
        size = path.stat().st_size
        started = await self.start_upload(tenant_id, user, name, size, mime, account_id)
        doc_id = UUID(started["document_id"])

        async def chunks() -> AsyncIterator[bytes]:
            """The file read in bounded pieces."""
            with path.open("rb") as source:
                # LOOP(import spool): cap=size / chunk_bytes reads; exit=end of the spool file
                while block := source.read(self.c.upload.chunk_bytes):
                    yield block

        try:
            await self.blobs.write_stream(
                quarantine_key(tenant_id, doc_id), chunks(), max_bytes=size
            )
        except PartTooLarge:
            raise DocumentError("size_mismatch") from None
        return await self.complete(tenant_id, doc_id)

    async def complete(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any]:
        """Check what arrived (its size, from storage metadata: a large file is never read in
        the request) and enqueue the scan, which also hashes it. Nothing is parsed here."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id, lock=True)
            if row is None:
                raise DocumentError("not_found", "The document was not found.")
            if row.status != "awaiting_upload":
                return self._view(row)
            try:
                size = await self.blobs.size(row.quarantine_key)
            except BlobMissing:
                raise DocumentError("not_uploaded") from None
            if size != row.declared_bytes:
                await repo.reject(s, document_id, "size_mismatch", None)
                await self.blobs.delete(row.quarantine_key)
            elif size > await self.max_file_bytes(s):
                await repo.reject(s, document_id, "too_large", None)
                await self.blobs.delete(row.quarantine_key)
            else:
                await repo.set_status(
                    s, document_id, "quarantined", size_bytes=size, upload_token_sha256=None
                )
            row = await repo.get(s, document_id)
        if row.status == "quarantined":
            await self.scheduler.enqueue_document(tenant_id, document_id)
        return self._view(row)

    def _hash(self, key: str) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with self.blobs.open(key) as reader:
            # LOOP(hash uploaded document): cap=size / 1 MiB reads; exit=end of object
            while block := reader.read(1 << 20):
                digest.update(block)
                size += len(block)
        return size, digest.hexdigest()

    # ------------------------------------------------------------------ processing

    async def process(self, tenant_id: UUID, document_id: UUID) -> str:
        """Scan, plan, read and index one document (see the module docstring). Returns the
        status, ``busy`` when another worker holds the step (or every part slot of the client is
        taken), ``noop`` when there is nothing to do, ``parsing`` or ``indexing`` when this
        request's share is done and the next task continues. Any number of workers may run this
        for one document at once: they share its parts. Every call logs ``documents.processed``
        with the status it reached (audit 2026-10-16: the pipeline had no structured events)."""
        import time

        started = time.monotonic()
        status = await self._process(tenant_id, document_id)
        _log.info(
            "documents.processed",
            extra={
                "tenant_id": str(tenant_id),
                "state": status,
                "latency_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return status

    async def _process(self, tenant_id: UUID, document_id: UUID) -> str:
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id)
        if row is None:
            return "noop"
        if row.status in ("quarantined", "scanning") or (
            row.status == "parsing" and not row.parts_total
        ):
            status = await self._leased(
                tenant_id, document_id, ("quarantined", "scanning", "parsing"), self._prepare
            )
            if status != "parsing":
                return status
            row = await self._row(tenant_id, document_id)
        if row is not None and row.status == "parsing":
            status = await self.read_parts(tenant_id, document_id)
            if status != "parsed":
                return status
            row = await self._row(tenant_id, document_id)
        if row is not None and row.status in ("parsed", "indexing") and row.account_id is not None:
            return await self._leased(tenant_id, document_id, ("parsed", "indexing"), self._index)
        return "noop" if row is None else str(row.status)

    async def _row(self, tenant_id: UUID, document_id: UUID) -> Any:
        async with self.txn(tenant_id) as s:
            return await repo.get(s, document_id)

    async def _leased(
        self,
        tenant_id: UUID,
        document_id: UUID,
        statuses: tuple[str, ...],
        step: Callable[[UUID, Any], Awaitable[str]],
    ) -> str:
        """Run one step of the document under its lease, renewed while it runs."""
        ttl = self.c.parsing.lease_ttl_s
        async with self.txn(tenant_id) as s:
            row = await repo.claim(s, document_id, self.worker, ttl, statuses)
            current = None if row is not None else await repo.get(s, document_id)
        if row is None:
            # another worker holds a document that still has work: retry later; a finished,
            # rejected or missing document (a duplicate task) has nothing to do
            return "busy" if current is not None and current.status in statuses else "noop"

        async def renew() -> bool:
            async with self.txn(tenant_id) as s:
                return await repo.renew_document(s, document_id, self.worker, ttl)

        try:
            async with self._heartbeat(renew):
                return await step(tenant_id, row)
        finally:
            async with self.txn(tenant_id) as s:
                await repo.release(s, document_id, self.worker)

    def _heartbeat(self, renew: Callable[[], Awaitable[bool]]) -> Any:
        """Renew a lease every third of its life while the body runs; ``.lost`` turns true when
        another worker took it over (the body stops at its next write)."""
        from contextlib import asynccontextmanager

        every = max(1.0, self.c.parsing.lease_ttl_s / 3)

        class Beat:
            lost = False

        @asynccontextmanager
        async def beating() -> AsyncIterator[Beat]:
            beat = Beat()

            async def loop() -> None:
                # LOOP(lease heartbeat): cap=the step's duration / (lease_ttl_s / 3) renewals;
                # exit=step finished (cancelled), lease lost
                while not beat.lost:
                    await asyncio.sleep(every)
                    if not await renew():
                        beat.lost = True

            task = asyncio.create_task(loop())
            try:
                yield beat
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        return beating()

    async def _prepare(self, tenant_id: UUID, row: Any) -> str:
        status = str(row.status)
        if status in ("quarantined", "scanning"):
            status = await self._scan(tenant_id, row)
            if status != "parsing":
                return status
            async with self.txn(tenant_id) as s:
                row = await repo.get(s, row.id)
        return await self._plan(tenant_id, row)

    async def _scan(self, tenant_id: UUID, row: Any) -> str:
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "scanning")
        try:
            reader = self.blobs.open(row.quarantine_key)
        except BlobMissing:
            async with self.txn(tenant_id) as s:
                await repo.reject(s, row.id, "not_uploaded", None)
            return "rejected"
        with reader:
            kind = await asyncio.to_thread(detect, reader, row.name)
            reason = self._type_problem(kind, row.declared_type)
            reader.seek(0)
            verdict = None if reason else await self.scanner.scan(reader)
        if reason is None and verdict is not None:
            if verdict.result == "infected":
                reason = "infected"
            elif verdict.result != "clean":
                reason = "scan_unavailable"
        if reason is not None:
            detail = (
                f"{verdict.engine}:{verdict.signature}"
                if verdict is not None and verdict.signature
                else None
            )
            async with self.txn(tenant_id) as s:
                await repo.reject(s, row.id, reason, detail)
                await self._audit(s, "document.reject", row.id, "denied")
            if reason != "scan_unavailable":
                await self.blobs.delete(row.quarantine_key)
            return "rejected"
        size, sha = await asyncio.to_thread(self._hash, row.quarantine_key)
        async with self.txn(tenant_id) as s:
            existing = await repo.raw_file(s, sha)
        if existing is None:
            target = raw_key(tenant_id, sha)
            await self.blobs.move(row.quarantine_key, target)
        else:  # the same bytes were stored before: one copy serves both documents
            target = str(existing.object_key)
            await self.blobs.delete(row.quarantine_key)
        async with self.txn(tenant_id) as s:
            await repo.register_raw_file(s, sha, size, target, row.id)
            await repo.set_status(
                s,
                row.id,
                "parsing",
                object_key=target,
                detected_type=kind,
                sha256=sha,
                size_bytes=size,
            )
        return "parsing"

    def _type_problem(self, kind: str, declared: str) -> str | None:
        if kind in self.c.upload.audio_types:
            return "audio_not_supported"
        if kind not in self.c.upload.document_types:
            return "wrong_type"
        # a CSV and a TXT are both text; anything binary must be what its name says
        text_kinds = {"csv", "txt", "md"}
        if kind != declared and not (kind in text_kinds and declared in text_kinds):
            return "wrong_type"
        return None

    async def _plan(self, tenant_id: UUID, row: Any) -> str:
        """Split the document into parts (planned in the sandbox: it reads the file); a document
        the planner cannot split is one part. More workers are asked to share the parts."""
        cfg = self.c
        try:
            async with self._local(row.object_key) as path:
                plans = await plan_in_sandbox(
                    path,
                    row.detected_type,
                    row.name,
                    timeout_s=cfg.sandbox.timeout_s,
                    memory_mb=cfg.sandbox.memory_mb,
                    cpu_s=cfg.sandbox.cpu_s,
                    part_bytes=cfg.parsing.part_bytes,
                    pages_per_part=cfg.parsing.pages_per_part,
                )
        except SandboxFailed:
            plans = [{"cursor": {}}]
        async with self.txn(tenant_id) as s:
            await repo.create_parts(s, row.id, plans)
        for _ in range(min(len(plans), cfg.parsing.max_parallel_parts) - 1):
            await self.scheduler.enqueue_document(tenant_id, row.id)
        return "parsing"

    async def read_parts(self, tenant_id: UUID, document_id: UUID) -> str:
        """Read parts of the document until none is left for this worker, then, when every part
        is read, decide the document (``parsed``, or ``failed`` naming the failed parts)."""
        cfg = self.c.parsing
        read = 0
        # LOOP(document parts): cap=parts_total parts per worker, parts_per_request per call;
        # exit=no part left, client busy, request's share read (the next task continues)
        while True:
            if self.parts_per_request is not None and read >= self.parts_per_request:
                async with self.txn(tenant_id) as s:
                    pending = any(p.status == "pending" for p in await repo.parts(s, document_id))
                if pending:
                    await self.scheduler.enqueue_document(tenant_id, document_id)
                    return "parsing"
                break
            async with self.txn(tenant_id) as s:
                part = await repo.claim_part(
                    s, document_id, self.worker, cfg.lease_ttl_s, cfg.max_parallel_parts
                )
                doc = await repo.get(s, document_id) if part not in (None, "busy") else None
            if part is None:
                break
            if part == "busy":
                return "busy"
            if doc is None:
                return "noop"
            await self._read_part(tenant_id, doc, part)
            read += 1
        return await self._decide(tenant_id, document_id)

    async def _read_part(self, tenant_id: UUID, doc: Any, part: Any) -> None:
        cfg = self.c
        part_no = int(part.part_no)
        async with self.txn(tenant_id) as s:
            tier = await repo.policy_tier(s, cfg.redaction.default_tier)
            exclude = await repo.tenant_domains(s)
            await repo.clear_part(s, doc.id, part_no)  # an interrupted read starts clean
        cursor = dict(part.start_cursor or {})
        redactor = Redactor(tier)
        chunker = Chunker(
            cfg.chunking,
            doc.name,
            redactor,
            cfg.parsing.max_passages,
            first_seq=part_no * repo.PART_SPAN,
            shape_log=ShapeLog(str(tenant_id), str(doc.detected_type or "")),
        )
        header = cursor.get("header")
        if header:
            chunker.headers[str(cursor.get("table") or doc.name)] = [str(h) for h in header]
        resolver = Resolver(cfg.company, exclude)
        pages: set[tuple[str, Any]] = set()
        batch: list[Passage] = []
        written = 0
        members_read: set[str] = set()  # zip members the parser read (skipped ones apart)
        skipped: list[dict[str, str]] = []  # zip members the parser listed but did not read
        ingested_at = self.clock.now().isoformat()  # B4: one stamp for the whole document

        async def renew() -> bool:
            async with self.txn(tenant_id) as s:
                return await repo.renew_part(
                    s, doc.id, part_no, self.worker, cfg.parsing.lease_ttl_s
                )

        table_ids: dict[str, UUID] = {}  # the table store records this part writes into

        async def flush(beat: Any) -> bool:
            nonlocal written, batch
            if beat.lost:
                return False
            entries = [
                (p.seq, ph, kind, orig)
                for p in batch
                for ph, kind, orig in redactor.present(p.text)
            ]
            async with self.txn(tenant_id) as s:
                if not await repo.alive(s, doc.id):
                    return False
                await repo.write_passages(
                    s, doc.id, doc.account_id, batch, entries, ingested_at=ingested_at
                )
                # every table row, whole and typed, into the store (queried, never sampled)
                by_table: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
                for p in batch:
                    if p.kind == "crm_row" and p.metadata:
                        by_table.setdefault(str(p.location["table"]), []).append(
                            (int(p.location["row"]), p.seq, p.metadata)
                        )
                for table, rows in by_table.items():
                    if table not in table_ids:
                        schema = chunker.tables[table]
                        table_ids[table] = await repo.ensure_table(
                            s, doc.id, doc.account_id, table, schema.member
                        )
                    await repo.write_table_rows(
                        s, table_ids[table], doc.id, doc.account_id, part_no, rows
                    )
            written += len(batch)
            batch = []
            return True

        spec = {
            "cursor": cursor,
            "end_offset": part.end_offset,
            "end_page": part.end_page,
        }
        try:
            async with self._heartbeat(renew) as beat, self._local(doc.object_key) as path:
                async for unit in self._units(path, doc, spec):
                    if unit.fatal and unit.check == "unsupported_type":
                        raise SandboxFailed("unsupported_type")
                    if unit.rows is not None and not unit.header and header:
                        unit.header = [str(h) for h in header]
                    for key in ("page", "slide", "sheet"):
                        if key in unit.location:
                            pages.add((key, unit.location[key]))
                    if len(pages) > cfg.parsing.max_pages:
                        raise SandboxFailed("page_limit")
                    member = unit.location.get("member")
                    if member:
                        # a member the parser listed but did not read is skipped, by name; every
                        # other member counts as read (an image without text included), so read
                        # plus skipped is the archive's file count
                        checks = {str((f or {}).get("check") or "") for f in unit.findings}
                        hit = sorted(checks & SKIP_CHECKS.keys())
                        if hit:
                            if len(skipped) < 200:
                                skipped.append({"member": str(member), "check": hit[0]})
                        else:
                            members_read.add(str(member))
                    resolver.feed(unit)
                    batch.extend(chunker.passages([unit], final=False))
                    if len(batch) >= cfg.parsing.write_batch and not await flush(beat):
                        return  # the lease was lost, or the document deleted
                batch.extend(chunker.finish())  # the part's last short section, merged
                if batch and not await flush(beat):
                    return
                if beat.lost:
                    return
                if table_ids:
                    async with self.txn(tenant_id) as s:
                        for table, table_id in table_ids.items():
                            await repo.set_table_schema(s, table_id, chunker.tables[table].record())
        except SandboxFailed as failed:
            async with self.txn(tenant_id) as s:
                await repo.fail_part(s, doc.id, part_no, self.worker, f"parse:{failed.code}")
            return
        except TableIngestTruncated as cut:
            # a table past the passage cap fails this member outright: a table stored in part
            # would answer a count or a filter wrongly (documents keep the truncated flag)
            _log.warning(
                "documents.table_truncated",
                extra={"tenant_id": str(tenant_id), "rows": cut.total, "count": cut.kept},
            )
            async with self.txn(tenant_id) as s:
                await repo.fail_part(s, doc.id, part_no, self.worker, "parse:table_truncated")
            return
        evidence = {
            "company": resolver.state(),
            "redactions": len(redactor.entries()),
            "truncated": chunker.truncated,
            "members_read": len(members_read),
            "skipped": skipped,
        }
        async with self.txn(tenant_id) as s:
            await repo.finish_part(s, doc.id, part_no, self.worker, written, evidence)

    async def _decide(self, tenant_id: UUID, document_id: UUID) -> str:
        """Once no part is pending or running: ``parsed`` with the company decided from every
        part's evidence, or ``failed`` when a part failed (the parts read are kept)."""
        async with self.txn(tenant_id) as s:
            doc = await repo.lock(s, document_id)
            if doc is None:
                return "noop"
            if doc.status != "parsing":
                return str(doc.status)
            parts = await repo.parts(s, document_id)
            if any(p.status in ("pending", "running") for p in parts):
                return "parsing"
            passages = sum(int(p.passages) for p in parts)
            if any(p.status == "failed" for p in parts):
                await repo.set_status(
                    s, document_id, "failed", stop_detail="parts_failed", passages=passages
                )
                return "failed"
            if not passages:
                await repo.set_status(s, document_id, "failed", stop_detail="parse:empty")
                return "failed"
            resolver = Resolver(self.c.company, await repo.tenant_domains(s))
            for p in parts:
                resolver.absorb((p.evidence or {}).get("company"))
            company = resolver.decide()
            if any((p.evidence or {}).get("truncated") for p in parts):
                company["truncated_at_passages"] = self.c.parsing.max_passages
            tier = await repo.policy_tier(s, self.c.redaction.default_tier)
            await repo.set_status(
                s,
                document_id,
                "parsed",
                passages=passages,
                redactions=sum(int((p.evidence or {}).get("redactions") or 0) for p in parts),
                redaction_tier=tier,
                detected_company=company,
            )
            await self._audit(s, "document.parse", document_id, "ok")
        return "parsed"

    async def retry_part(self, tenant_id: UUID, document_id: UUID, part_no: int) -> dict[str, Any]:
        """Try a failed part again: it is queued again and the document reads on."""
        async with self.txn(tenant_id) as s:
            doc = await repo.lock(s, document_id)
            if doc is None:
                raise DocumentError("not_found", "The document was not found.")
            if not await repo.retry_part(s, document_id, part_no):
                raise DocumentError("part_not_failed", "This part has not failed.")
            if doc.status == "failed":
                await repo.set_status(s, document_id, "parsing", stop_detail=None)
        await self.scheduler.enqueue_document(tenant_id, document_id)
        return await self.status(tenant_id, document_id)

    async def _units(self, path: str, row: Any, part: dict[str, Any]) -> AsyncIterator[ParsedUnit]:
        async for unit in parse_in_sandbox(
            path,
            row.detected_type,
            row.name,
            timeout_s=self.c.sandbox.timeout_s,
            memory_mb=self.c.sandbox.memory_mb,
            cpu_s=self.c.sandbox.cpu_s,
            max_text_bytes=self.c.parsing.max_text_bytes,
            part=part,
        ):
            yield unit

    def _local(self, key: str) -> Any:
        """A local file path for the sandbox: the store's own file, or a private temp copy."""
        from contextlib import asynccontextmanager

        blobs = self.blobs

        @asynccontextmanager
        async def path() -> AsyncIterator[str]:
            local = getattr(blobs, "path", None)
            if callable(local):
                yield str(local(key))
                return
            fd, tmp = tempfile.mkstemp(prefix="doc-")
            try:
                with os.fdopen(fd, "wb") as out, blobs.open(key) as reader:
                    # LOOP(copy document for the sandbox): cap=size / 1 MiB; exit=end of object
                    while block := reader.read(1 << 20):
                        out.write(block)
                yield tmp
            finally:
                os.unlink(tmp)

        return path()

    async def _index(self, tenant_id: UUID, row: Any) -> str:
        """Embed and index the passages page by page, continuing after the last page indexed.
        One call embeds for at most ``index_seconds_per_request`` (when set) and then queues the
        next task, which continues from the last page written (audit 2026-10-16: a
        37,000-passage file ran one request into the hour-long Cloud Run limit six times, and
        every retry started a fresh hour). Each page logs ``documents.indexed_page``."""
        import time

        cfg = self.c
        started = time.monotonic()
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexing")
        after = int(row.indexed_through) if row.indexed_through is not None else -1
        total = int(row.passages or 0)
        pages = 0
        # LOOP(index pages): cap=passages / index_batch pages, or the request's time budget;
        # exit=no passage left, deleted, budget spent (the next task continues)
        while True:
            async with self.txn(tenant_id) as s:
                rows = await repo.passages_after(s, row.id, after, cfg.parsing.index_batch)
            if not rows:
                break
            vectors: list[list[float]] = []
            semantic: list[list[float]] | None = [] if self.semantic is not None else None
            batch = cfg.embedding.batch
            for i in range(0, len(rows), batch):
                texts = [r.content for r in rows[i : i + batch]]
                vectors.extend(await self.embedder.embed(texts))
                if semantic is not None and self.semantic is not None:
                    semantic.extend(await self.semantic.embed(texts))
                await self._meter(tenant_id, row.id, texts)
            async with self.txn(tenant_id) as s:
                if not await repo.index_page(
                    s,
                    row.id,
                    row.account_id,
                    rows,
                    vectors,
                    self.embedder.model,
                    semantic,
                    self.semantic.model if self.semantic is not None else None,
                ):
                    return "noop"  # deleted while it was being embedded
            after = int(rows[-1].seq)
            pages += 1
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _log.info(
                "documents.indexed_page",
                extra={
                    "tenant_id": str(tenant_id),
                    "account_id": str(row.account_id),
                    "pages": pages,
                    "count": after + 1,
                    "rows": total,
                    "latency_ms": elapsed_ms,
                },
            )
            budget = self.index_seconds_per_request
            if (
                budget is not None
                and elapsed_ms >= budget * 1000
                and len(rows) >= cfg.parsing.index_batch
            ):
                # the request's share is done; the next task continues after this page
                await self.scheduler.enqueue_document(tenant_id, row.id)
                return "indexing"
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexed")
        await self._import_deliverables(tenant_id, row)
        _log.info(
            "documents.indexed",
            extra={
                "tenant_id": str(tenant_id),
                "account_id": str(row.account_id),
                "pages": pages,
                "count": after + 1,
                "latency_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return "indexed"

    async def _import_deliverables(self, tenant_id: UUID, row: Any) -> None:
        """The rows of an uploaded deliverable workbook as job postings, people and technologies
        of the account (``documents.deliverables``); a failure is logged and never stops
        indexing (the passages are indexed either way)."""
        from documents.deliverables import import_document

        now = self.clock.now()
        try:
            counts = await import_document(self.txn, tenant_id, row, now=now)
            async with self.txn(tenant_id) as s:
                await repo.mark_imported(s, row.id, now)
        except Exception as error:  # noqa: BLE001 - the import never fails the document
            from documents.deliverables import error_code

            _log.warning(
                "documents.import_failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "error_class": type(error).__name__,
                    "error_code": error_code(error)[:80],
                },
            )
            return  # not marked: the sweep's backfill reads it again
        written = sum(v for k, v in counts.items() if k.endswith("_written"))
        if counts:
            _log.info(
                "documents.imported",
                extra={
                    "tenant_id": str(tenant_id),
                    "account_id": str(row.account_id),
                    "count": written,
                    "rows": sum(v for k, v in counts.items() if k.endswith("_rows")),
                },
            )

    async def _meter(self, tenant_id: UUID, document_id: UUID, texts: list[str]) -> None:
        """Every embedding batch is logged with its model and cost (the file model call log)."""
        from decimal import Decimal

        tokens = sum(int(len(t) / self.c.chunking.chars_per_token) + 1 for t in texts)
        key = hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()
        async with self.txn(tenant_id) as s:
            await repo.log_embedding_call(
                s,
                document_id=document_id,
                model=self.embedder.model,
                key=f"embed:{self.embedder.model}:{key}",
                input_tokens=tokens,
                cost=Decimal(str(round(self.embedder.cost_usd(texts), 6))),
            )

    async def _audit(self, s: AsyncSession, action: str, document_id: UUID, outcome: str) -> None:
        from audit.events import record
        from audit.routes_catalog import action_event

        await record(
            s,
            action_event(
                audit_class="run",
                action=action,
                resource_type="document",
                resource_id=str(document_id),
                actor=self.worker,
                actor_kind="system",
                at=self.clock.now(),
                route=None,
                request_id=None,
                outcome=outcome,  # type: ignore[arg-type]
            ),
        )

    # ------------------------------------------------------------------ company and lifecycle

    async def confirm_company(
        self, tenant_id: UUID, document_ids: list[UUID], account_id: UUID
    ) -> list[UUID]:
        """Bind documents to the account the user confirmed, then index the parsed ones."""
        async with self.txn(tenant_id) as s:
            rows = await repo.many(s, document_ids)
            if len(rows) != len(set(document_ids)):
                raise DocumentError("not_found", "A document was not found.")
            if any(r.account_id not in (None, account_id) for r in rows):
                raise DocumentError(
                    "already_bound", "A document already belongs to another account."
                )
            await repo.bind_account(s, document_ids, account_id)
        for r in rows:
            if r.status == "parsed":
                await self.scheduler.enqueue_document(tenant_id, r.id)
        return [r.id for r in rows]

    async def delete(self, tenant_id: UUID, document_id: UUID, user: str) -> dict[str, Any]:
        """See the module docstring. Returns the counts removed and marked."""
        from account_intelligence.grading.repo import mark_document_facts

        async with self.txn(tenant_id) as s:
            out = await repo.delete(s, document_id, user)
            if out is None:
                raise DocumentError("not_found", "The document was not found.")
            row, chunks = out
            marked = await mark_document_facts(s, document_id, row.account_id)
            await repo.record_deletion(s, row, chunks, marked, user)
        if row.quarantine_key:
            await self.blobs.delete(row.quarantine_key)
        if row.object_key:
            # the raw object is shared by every document of the tenant with the same sha256
            # (0078): delete it, and its registry row, only when this was the last one
            shared = False
            if row.sha256:
                async with self.txn(tenant_id) as s:
                    shared = await repo.documents_with_sha(s, str(row.sha256)) > 0
                    if not shared:
                        await repo.remove_raw_file(s, str(row.sha256))
            if not shared:
                await self.blobs.delete(row.object_key)
        return {"document_id": str(document_id), "chunks_removed": chunks, "facts_marked": marked}

    async def status(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any]:
        """The document's status view."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id)
            counts = await repo.part_counts(s, [document_id]) if row is not None else {}
        if row is None:
            raise DocumentError("not_found", "The document was not found.")
        return self._view(row, counts.get(row.id))

    async def list(self, tenant_id: UUID, account_id: UUID | None) -> list[dict[str, Any]]:
        """The tenant's documents (one account's when given)."""
        async with self.txn(tenant_id) as s:
            rows = await repo.list_for(s, account_id)
            counts = await repo.part_counts(s, [r.id for r in rows])
        return [self._view(r, counts.get(r.id)) for r in rows]

    @staticmethod
    def _view(row: Any, parts: dict[str, Any] | None = None) -> dict[str, Any]:
        stage = {
            "awaiting_upload": "uploading", "quarantined": "scanning", "scanning": "scanning",
            "parsing": "parsing", "parsed": "parsed", "indexing": "indexing",
            "indexed": "indexed", "rejected": "failed", "failed": "failed",
        }[row.status]  # fmt: skip
        company = row.detected_company or {}
        return {
            "document_id": str(row.id),
            "account_id": str(row.account_id) if row.account_id else None,
            "name": row.name,
            "status": row.status,
            "stage": stage,
            "declared_type": row.declared_type,
            "detected_type": row.detected_type,
            "size_bytes": row.size_bytes if row.size_bytes is not None else row.declared_bytes,
            "reason": row.reject_reason or (row.stop_detail if row.status == "failed" else None),
            "message": MESSAGES.get(row.reject_reason or "")
            if row.reject_reason
            else (
                MESSAGES["parts_failed"]
                if row.stop_detail == "parts_failed"
                else ("The file could not be read." if row.status == "failed" else None)
            ),
            "passages": row.passages or (parts or {}).get("passages", 0),
            "parts_total": int(row.parts_total or 0),
            "parts_done": int((parts or {}).get("done", 0)),
            # passages indexed so far (parts number their passages from 1, so the sequence
            # indexed through is the count): the file list says "Indexing 3,200 of 22,380"
            "indexed_passages": int(row.indexed_through or 0)
            if row.status in ("indexing", "indexed")
            else 0,
            # a zip: how many of its files were read, and the ones that were not, by name
            "members_read": int((parts or {}).get("members_read", 0)),
            "members_skipped": [
                {
                    "member": str(m.get("member") or ""),
                    "reason": SKIP_CHECKS.get(str(m.get("check")), str(m.get("check") or "")),
                }
                for m in (parts or {}).get("skipped", [])
            ][:100],
            "parts_failed": [
                {"part": int(f["part"]) + 1, "reason": part_message(f.get("failure"))}
                for f in (parts or {}).get("failed", [])
            ],
            "redactions": row.redactions,
            "redaction_tier": row.redaction_tier,
            "authoritative": bool(row.authoritative),
            "company": company or None,
            "needs_company": row.account_id is None and row.status == "parsed",
            "created_at": row.created_at.isoformat(),
        }


async def sweep_documents(
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    scheduler: Any,
    now: datetime,
    *,
    orphan_after_s: int,
) -> list[UUID]:
    """Documents whose worker died mid-step (lease expired) are enqueued again; each step is
    idempotent (a re-parse clears the document's passages first)."""
    from sqlalchemy import text

    stale = now - timedelta(seconds=orphan_after_s)
    async with sessions() as s:
        rows = (
            await s.execute(
                text("SELECT tenant_id, document_id FROM documents_orphaned(:b) LIMIT 200"),
                {"b": stale},
            )
        ).all()
        parted = (
            await s.execute(
                text(
                    "SELECT DISTINCT tenant_id, document_id FROM document_parts_orphaned(:b)"
                    " LIMIT 200"
                ),
                {"b": stale},
            )
        ).all()
    found: list[tuple[UUID, UUID]] = []
    for r in [*rows, *parted]:
        key = (UUID(str(r.tenant_id)), UUID(str(r.document_id)))
        if key not in found:
            found.append(key)
    for tenant_id, document_id in found:
        await scheduler.enqueue_document(tenant_id, document_id)
    return [d for _, d in found]
