"""Uploaded documents from upload to index (input mode "file"), at any size.

::

    start_upload -> chunks straight to object storage (a resumable session; the API's
                    token-checked content route only where the store has none)
      -> complete (size checked; status quarantined; processing enqueued)
      -> process: type check from content -> malware scan -> sha256 -> promote out of quarantine
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
* Deleting a document removes its index rows, passages, redaction map, stored objects and row,
  and marks every fact that cites it for re-evaluation.
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
from documents.chunking import Chunker, Passage
from documents.company import Resolver
from documents.config import InputModesConfig, load_config
from documents.embed import EmbeddingPort
from documents.redact import Redactor
from documents.sandbox import ParsedUnit, SandboxFailed, parse_in_sandbox, plan_in_sandbox
from documents.scan import ScanPort
from research_files.blobstore import BlobMissing, BlobStore, PartTooLarge
from research_files.detect import by_name, detect

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
PART_MESSAGES = {
    "parse:parse_timeout": "This part took too long to read.",
    "parse:text_limit": "This part holds more text than one part may.",
    "parse:page_limit": "This part has more pages than one part may.",
    "parse:unit_too_large": "One row or line in this part is too large to read.",
    "parse:unsupported_type": "This part's content could not be read.",
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
    """Where a scanned document lives."""
    return f"tenants/{tenant_id}/documents/{document_id}/raw"


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
        taken), ``noop`` when there is nothing to do. Any number of workers may run this for one
        document at once: they share its parts."""
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
        target = document_key(tenant_id, row.id)
        await self.blobs.move(row.quarantine_key, target)
        async with self.txn(tenant_id) as s:
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
        )
        header = cursor.get("header")
        if header:
            chunker.headers[str(cursor.get("table") or doc.name)] = [str(h) for h in header]
        resolver = Resolver(cfg.company, exclude)
        pages: set[tuple[str, Any]] = set()
        batch: list[Passage] = []
        written = 0

        async def renew() -> bool:
            async with self.txn(tenant_id) as s:
                return await repo.renew_part(
                    s, doc.id, part_no, self.worker, cfg.parsing.lease_ttl_s
                )

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
                await repo.write_passages(s, doc.id, doc.account_id, batch, entries)
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
                    resolver.feed(unit)
                    batch.extend(chunker.passages([unit]))
                    if len(batch) >= cfg.parsing.write_batch and not await flush(beat):
                        return  # the lease was lost, or the document deleted
                if batch and not await flush(beat):
                    return
                if beat.lost:
                    return
        except SandboxFailed as failed:
            async with self.txn(tenant_id) as s:
                await repo.fail_part(s, doc.id, part_no, self.worker, f"parse:{failed.code}")
            return
        evidence = {
            "company": resolver.state(),
            "redactions": len(redactor.entries()),
            "truncated": chunker.truncated,
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
        """Embed and index the passages page by page, continuing after the last page indexed."""
        cfg = self.c
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexing")
        after = int(row.indexed_through) if row.indexed_through is not None else -1
        # LOOP(index pages): cap=passages / index_batch pages; exit=no passage left, deleted
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
                if semantic is not None:
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
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexed")
        return "indexed"

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
        for key in (row.quarantine_key, row.object_key):
            if key:
                await self.blobs.delete(key)
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
