"""Uploaded documents from upload to index (input mode "file"), at any size.

::

    start_upload -> chunks straight to the quarantine bucket (a resumable session; the API's
                    token-checked content route only where the store has none)
      -> complete (size and CRC32C checked against what the browser reported; status
         quarantined; processing enqueued on Cloud Tasks)
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

* Where the upload goes (PR4 part B, 2026-11-04): with ``QUARANTINE_BUCKET`` set the container
  gives the service a second store (``quarantine``), a separate bucket with a one-day delete
  rule. Uploads wait there, the scan reads from there, and a clean file is copied into the
  documents store under its content-addressed ``raw_key`` and then removed from quarantine.
  Without one, quarantine is a prefix of the documents store (local development, and documents
  created before the bucket existed: an object not found in the quarantine store is looked for in
  the documents store). ``complete`` is the trigger: it compares the stored object's size and
  CRC32C (storage metadata; a missing browser checksum skips only the checksum) with what the
  browser reported, rejects ``size_mismatch`` / ``checksum_mismatch`` and deletes the object,
  and only then enqueues the scan on Cloud Tasks. No Pub/Sub. ``sweep_documents`` (every five
  minutes) is the safety net for a lost task, a dead worker, and for ``scan_failed`` files, which
  it scans again (``retry_failed_scans``, at most ``SCAN_ATTEMPTS`` attempts in all).
  ``mark_abandoned`` (daily) ends ``awaiting_upload`` rows older than 24 hours ``abandoned``.
* Nothing is parsed before the scan passed; a wrong type, an oversized file or an infected file
  ends ``rejected`` with the reason, and the parser never sees the bytes. A scanner that cannot
  be reached is not a verdict: the document is ``scan_failed`` (fail closed, never read, its
  quarantine object kept for the retry), not ``rejected`` and never clean.
* Audio is accepted only when a transcription vendor is configured; today it is rejected with a
  clear message.
* The company is matched first (PR5), since the import and the index both depend on it. A
  document whose company is resolved to its account's domain (uploaded to that account), or to the
  domain of one of the owner's accounts (uploaded with none), is bound and goes on: the
  deliverables import first (the account page fills), then the embedding as the next task. Any
  other upload (no company, ambiguous, or another company than its account) waits as
  ``needs_account`` with the detected company; nothing is imported or indexed until the user
  confirms the account (``confirm_company``). Ambiguity is never resolved by guessing.
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
import functools
import hashlib
import os
import re
import secrets
import tempfile
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from datamodel.watch_config import set_account_watch_config
from documents import repo
from documents import workspace as ws
from documents.chunking import Chunker, Passage, TableIngestTruncated
from documents.company import Resolver, registrable
from documents.config import InputModesConfig, load_config
from documents.embed import EmbeddingPort
from documents.redact import Redactor
from documents.sandbox import ParsedUnit, SandboxFailed, parse_in_sandbox, plan_in_sandbox
from documents.scan import ScanPort
from documents.shapes import ShapeLog
from obs.logging import log_context, logger
from research_files.blobstore import BlobMissing, BlobStore, PartTooLarge, copy_between
from research_files.detect import by_name, detect

_log = logger(__name__)
# the document lease of the running step (``_leased``): its owner and heartbeat, read by the
# index loop so a call that lost its lease stops before it writes another page
_LEASE: ContextVar[tuple[str, Any] | None] = ContextVar("documents_lease", default=None)


_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def _doc_logged(fn: _F) -> _F:
    """Run a step of the document pipeline (scan, read, index, import, analyse) with the tenant,
    account and document on every log line it emits (ids only; ``obs.logging.log_context``).
    The step is ``(self, tenant_id, row_or_document, ...)``."""

    @functools.wraps(fn)
    async def wrapper(self: Any, tenant_id: UUID, subject: Any, *args: Any, **kw: Any) -> Any:
        with log_context(
            tenant_id=tenant_id,
            account_id=getattr(subject, "account_id", None),
            document_id=getattr(subject, "id", None),
        ):
            return await fn(self, tenant_id, subject, *args, **kw)

    return cast(_F, wrapper)


TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_NAME = re.compile(r"[^\w .()\-]+")
ACTIVE = ("quarantined", "scanning", "parsing", "indexing")
# scan attempts in all (the first included) before a file stays ``scan_failed``
SCAN_ATTEMPTS = 3
# the largest file inside an uploaded zip served on its own (``member_file``); the whole upload
# is capped at 100 MB by the declare step, so this refuses nothing a real package holds
MEMBER_FILE_MAX = 100 * 1024 * 1024
SCAN_GIVEN_UP = (
    "The malware scanner could not be reached; the file was not read. Upload it again later."
)
ABANDONED = "The upload was never finished, so it was discarded. Upload the file again."
ABANDON_AFTER = timedelta(hours=24)
MESSAGES = {
    "too_large": "The file is larger than the per-file limit.",
    "wrong_type": "The file type is not supported, or its content does not match its name.",
    "audio_not_supported": (
        "Audio transcription is not set up for this workspace, so audio files cannot be"
        " researched yet. Upload a transcript instead."
    ),
    "infected": "The malware scan found a threat; the file was removed and never opened.",
    "scan_unavailable": (
        "The malware scanner could not be reached; the file was not read. It will be tried again."
    ),
    "size_mismatch": "The uploaded size does not match the declared size. Upload it again.",
    "checksum_mismatch": (
        "The uploaded file does not match what the browser sent (its checksum differs)."
        " Upload it again."
    ),
    "not_uploaded": "No file arrived for this upload. Upload it again.",
    "empty": "The file has no readable content.",
    "parts_failed": "Some parts of the file could not be read. Try those parts again.",
    "needs_account": "Pick the account this file belongs to.",
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
    # members skipped by the owner-only rule removed in 0115: they are read on a new upload
    "not_read_upload_again": "not read; upload the zip again to read it",
    **ws.SKIP_LABELS,
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


def _zip_listing(path: str) -> list[tuple[str, int]]:
    """A zip's files in archive order with their declared sizes, from its central directory
    (names only; no member is opened); empty when it cannot be listed."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            return [(i.filename, int(i.file_size)) for i in archive.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, EOFError):
        return []


def _member_rows(
    listing: Sequence[tuple[str, int]],
    roles: Mapping[str, ws.MemberRole],
    members_read: set[str],
    skipped: Sequence[Mapping[str, str]],
    per_member: Mapping[str, int],
    tables: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The ``document_members`` rows one part writes: every member it read or skipped, with its
    role, type, declared size, passages and tables (``documents.workspace`` for the roles)."""
    sizes = dict(listing)
    tables_of: Counter[str] = Counter(
        str(schema.member) for schema in tables.values() if getattr(schema, "member", None)
    )
    skip_of = {str(m["member"]): str(m["check"]) for m in skipped}
    rows: list[dict[str, Any]] = []
    for name in sorted(members_read | set(skip_of)):
        role = roles.get(name, ws.MemberRole.OTHER)
        rows.append(
            {
                "member_path": name,
                "role": str(role),
                "declared_type": by_name(name),
                "bytes": sizes.get(name),
                "status": "skipped" if name in skip_of else "read",
                "failure": skip_of.get(name),
                "passages": int(per_member.get(name, 0)),
                "tables": int(tables_of.get(name, 0)),
            }
        )
    return rows


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
    # the research payload store (posting texts of an imported workspace); None locally where
    # the container has none
    payloads: Any | None = None
    # True: the deliverables import is a step of its own and the embedding is queued as the next
    # task, so the account page fills before any vector is written (PR5); False runs both in one
    # call (the service tests that drive a document with a single ``process``)
    index_in_own_task: bool = True
    # the model analysis an indexed upload gets by itself (``app.auto_analysis.start`` bound to
    # the container; None in tests of the service alone): called as (tenant_id, document_id)
    analysis: Any | None = None
    # the quarantine bucket's store (``QUARANTINE_BUCKET``); None: quarantine is a prefix of
    # ``blobs``, as it was before the bucket existed
    quarantine: BlobStore | None = None

    def __post_init__(self) -> None:
        self.cfg = self.cfg or load_config()
        self.worker = self.worker or f"docs-{uuid4().hex[:8]}"

    @property
    def c(self) -> InputModesConfig:
        """The loaded configuration."""
        assert self.cfg is not None
        return self.cfg

    # ------------------------------------------------------------------ upload

    @property
    def _incoming(self) -> BlobStore:
        """Where new uploads wait for their scan."""
        return self.quarantine or self.blobs

    async def _holding(self, key: str) -> BlobStore:
        """The store that holds a quarantined object: the quarantine store, else (a document
        created before the bucket existed) the documents store. Neither: the quarantine
        store, which then answers ``BlobMissing``."""
        if self.quarantine is None:
            return self.blobs
        if await self.quarantine.exists(key):
            return self.quarantine
        if await self.blobs.exists(key):
            return self.blobs
        return self.quarantine

    async def _drop_quarantined(self, key: str) -> None:
        """Remove a quarantined object from wherever it is (no error when absent)."""
        await self._incoming.delete(key)
        if self.quarantine is not None:
            await self.blobs.delete(key)

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
                    await s.execute(
                        text("SELECT 1 FROM accounts WHERE id = :a AND deleted_at IS NULL"),
                        {"a": account_id},
                    )
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
        session = await self._incoming.resumable_upload(qkey, size, content_type, origin)
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
            size, _ = await self._incoming.write_stream(
                row.quarantine_key, chunks, max_bytes=int(row.declared_bytes)
            )
        except PartTooLarge:
            raise DocumentError("size_mismatch") from None
        return size

    async def complete(
        self,
        tenant_id: UUID,
        document_id: UUID,
        *,
        declared_size: int | None = None,
        declared_crc32c: str | None = None,
    ) -> dict[str, Any]:
        """Check what arrived and enqueue the scan, which also hashes it. Nothing is parsed here.
        The object's size and CRC32C come from storage metadata (a large file is never read in
        the request, except in the local store, which has no metadata). ``declared_size`` and
        ``declared_crc32c`` are what the browser reported: a difference ends the document
        ``rejected`` (``size_mismatch`` / ``checksum_mismatch``) and removes the object. A
        missing checksum (an older browser) skips
        only the checksum; the size is always checked against the size declared at the start."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id, lock=True)
            if row is None:
                raise DocumentError("not_found", "The document was not found.")
            if row.status != "awaiting_upload":
                return self._view(row)
            held = await self._holding(row.quarantine_key)
            try:
                size = await held.size(row.quarantine_key)
            except BlobMissing:
                raise DocumentError("not_uploaded") from None
            reason = None
            if size != row.declared_bytes or (declared_size is not None and size != declared_size):
                reason = "size_mismatch"
            elif size > await self.max_file_bytes(s):
                reason = "too_large"
            elif declared_crc32c and not await self._same_checksum(
                held, row.quarantine_key, declared_crc32c, tenant_id
            ):
                reason = "checksum_mismatch"
            if reason is not None:
                await repo.reject(s, document_id, reason, None)
                await self._drop_quarantined(row.quarantine_key)
            else:
                await repo.set_status(
                    s, document_id, "quarantined", size_bytes=size, upload_token_sha256=None
                )
            row = await repo.get(s, document_id)
        if row.status == "quarantined":
            await self.scheduler.enqueue_document(tenant_id, document_id)
        return self._view(row)

    async def _same_checksum(
        self, held: BlobStore, key: str, declared: str, tenant_id: UUID
    ) -> bool:
        """Whether the stored object's CRC32C is the one the browser reported. Storage that holds
        no checksum for the object cannot confirm or refute it: logged, then accepted (the scan
        hashes the bytes again)."""
        stored = await held.crc32c(key)
        if not stored:
            _log.warning("documents.crc32c_unavailable", extra={"tenant_id": str(tenant_id)})
            return True
        return secrets.compare_digest(stored, declared)

    def _hash(self, store: BlobStore, key: str) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with store.open(key) as reader:
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
        with log_context(tenant_id=tenant_id, document_id=document_id):
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
            # the import step runs once per document: an import that failed (or died) is not run
            # again here, where it would hold the embedding back on every task; the document is
            # indexed and the sweep's backfill tries the import again, at most three times
            # (``repo.claim_imports``; PR5 review B1, 2026-11-04)
            if (
                row.imported_at is None
                and (row.status == "parsed" or row.indexed_through is None)
                and not await self._import_tried(tenant_id, row.id)
            ):
                # PR5: the account's rows first, from the passages and the zip (no embedding
                # needed), so the account page fills while the vectors are still being written
                status = await self._leased(
                    tenant_id, document_id, ("parsed", "indexing"), self._import_step
                )
                if status in ("busy", "noop"):
                    return status
                if self.index_in_own_task:
                    await self.scheduler.enqueue_document(tenant_id, document_id)
                    return "indexing"
            return await self._leased(tenant_id, document_id, ("parsed", "indexing"), self._index)
        return "noop" if row is None else str(row.status)

    @_doc_logged
    async def _import_step(self, tenant_id: UUID, row: Any) -> str:
        """Import the uploaded deliverables of a bound document before it is embedded. The status
        moves to ``indexing`` first, so the sweep finds a document whose worker died here. A
        failed import is logged and left unmarked (the sweep's backfill reads it again) and never
        stops the indexing."""
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexing")
        await self._import_deliverables(tenant_id, row)
        return "indexing"

    async def _import_tried(self, tenant_id: UUID, document_id: UUID) -> bool:
        """Whether the document's import has been attempted (an import run exists for it)."""
        from documents import import_status

        async with self.txn(tenant_id) as s:
            return await import_status.attempted(s, tenant_id, document_id)

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
        """Run one step of the document under its lease, renewed while it runs. The lease owner
        is this call's own (the worker plus a per-call suffix): one instance serves several
        requests at once, and with the worker's name as owner a second task for the same
        document re-took the running call's lease, the first to finish released it for both,
        and a third instance took it as well (staging 2026-11-05: 20 of 46 pages embedded two
        or three times, the analysis started three times)."""
        ttl = self.c.parsing.lease_ttl_s
        owner = f"{self.worker}:{uuid4().hex[:8]}"
        async with self.txn(tenant_id) as s:
            row = await repo.claim(s, document_id, owner, ttl, statuses)
            current = None if row is not None else await repo.get(s, document_id)
        if row is None:
            # another worker holds a document that still has work: retry later; a finished,
            # rejected or missing document (a duplicate task) has nothing to do
            return "busy" if current is not None and current.status in statuses else "noop"

        async def renew() -> bool:
            async with self.txn(tenant_id) as s:
                return await repo.renew_document(s, document_id, owner, ttl)

        try:
            async with self._heartbeat(renew) as beat:
                token = _LEASE.set((owner, beat))
                try:
                    return await step(tenant_id, row)
                finally:
                    _LEASE.reset(token)
        finally:
            async with self.txn(tenant_id) as s:
                await repo.release(s, document_id, owner)

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

    @_doc_logged
    async def _scan(self, tenant_id: UUID, row: Any) -> str:
        async with self.txn(tenant_id) as s:
            await repo.begin_scan(s, row.id)
        held = await self._holding(row.quarantine_key)
        try:
            reader = held.open(row.quarantine_key)
        except BlobMissing:
            async with self.txn(tenant_id) as s:
                await repo.reject(s, row.id, "not_uploaded", None)
            return "rejected"
        with reader:
            kind = await asyncio.to_thread(detect, reader, row.name)
            reason = self._type_problem(kind, row.declared_type)
            reader.seek(0)
            verdict = None if reason else await self.scanner.scan(reader)
        if reason is None and verdict is not None and verdict.result == "unavailable":
            # not a verdict: fail closed without reading the file, keep it for the retry
            async with self.txn(tenant_id) as s:
                await repo.scan_failed(s, row.id, verdict.engine)
                await self._audit(s, "document.scan_failed", row.id, "error")
            _log.warning(
                "documents.scan_failed",
                extra={"tenant_id": str(tenant_id), "engine": verdict.engine},
            )
            return "scan_failed"
        if reason is None and verdict is not None and verdict.result != "clean":
            reason = "infected" if verdict.result == "infected" else "scan_unavailable"
        if reason is not None:
            detail = (
                f"{verdict.engine}:{verdict.signature}"
                if verdict is not None and verdict.signature
                else None
            )
            async with self.txn(tenant_id) as s:
                await repo.reject(s, row.id, reason, detail)
                await self._audit(s, "document.reject", row.id, "denied")
            await self._drop_quarantined(row.quarantine_key)
            return "rejected"
        size, sha = await asyncio.to_thread(self._hash, held, row.quarantine_key)
        async with self.txn(tenant_id) as s:
            existing = await repo.raw_file(s, sha)
        if existing is None:
            target = raw_key(tenant_id, sha)
            if held is self.blobs:
                await self.blobs.move(row.quarantine_key, target)
            else:  # the quarantine bucket: copy into the documents bucket, then empty it
                await copy_between(held, row.quarantine_key, self.blobs, target, size)
                await held.delete(row.quarantine_key)
        else:  # the same bytes were stored before: one copy serves both documents
            target = str(existing.object_key)
            await held.delete(row.quarantine_key)
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

    @_doc_logged
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
        skipped_total = 0  # all of them: the named list below holds 200 at most
        unread: set[str] = set()  # members this read lists but does not parse (their role)
        per_member: Counter[str] = Counter()  # passages each member produced
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
                listing = _zip_listing(path) if str(doc.detected_type or "") == "zip" else []
                names = [name for name, _ in listing]
                roles = ws.member_roles(names)
                if tier != "strict":
                    # the deliverables of a workspace package are the owner's finished files:
                    # stored word for word (the Standard patterns rewrote names and profile
                    # links of the UCB People List, 2026-11-05); Strict stays the tenant's call
                    chunker.verbatim_members = {
                        m for m, role in roles.items() if role in ws.DELIVERABLE_ROLES
                    }
                if part_no == 0 and ws.marker_missing(names):
                    # a deliverables/ folder but no RESTORE.md or MANIFEST.json: read as a
                    # workspace all the same (PR5), said once per document in the log
                    _log.warning(
                        "documents.workspace_marker_missing",
                        extra={"tenant_id": str(tenant_id), "document_id": str(doc.id)},
                    )
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
                    if member and (role_skip := ws.skip_check(roles, str(member))):
                        # a build script or intermediate of a workspace package: listed,
                        # counted once, never parsed into passages (documents.workspace)
                        if member not in unread:
                            unread.add(str(member))
                            skipped_total += 1
                            if len(skipped) < 200:
                                skipped.append({"member": str(member), "check": role_skip})
                        continue
                    if member:
                        # a member the parser listed but did not read is skipped, by name; every
                        # other member counts as read (an image without text included), so read
                        # plus skipped is the archive's file count
                        checks = {str((f or {}).get("check") or "") for f in unit.findings}
                        hit = sorted(checks & SKIP_CHECKS.keys())
                        if hit:
                            skipped_total += 1
                            if len(skipped) < 200:
                                skipped.append({"member": str(member), "check": hit[0]})
                        else:
                            members_read.add(str(member))
                    resolver.feed(unit)
                    new = list(chunker.passages([unit], final=False))
                    if member:
                        per_member[str(member)] += len(new)
                    batch.extend(new)
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
            "skipped_total": skipped_total,
        }
        async with self.txn(tenant_id) as s:
            await repo.finish_part(s, doc.id, part_no, self.worker, written, evidence)
            if listing:
                await repo.record_members(
                    s,
                    doc.id,
                    doc.account_id,
                    part_no,
                    _member_rows(listing, roles, members_read, skipped, per_member, chunker.tables),
                )

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
                if str(doc.detected_type or "") == "zip":
                    await self._record_failed_members(s, doc, parts)
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
            verdict, bind_to = await self._match(s, doc, company)
            if bind_to is not None and doc.account_id is None:
                await repo.bind_account(s, [document_id], bind_to)
            status = "parsed" if verdict == "proceed" else "needs_account"
            await repo.set_status(
                s,
                document_id,
                status,
                passages=passages,
                redactions=sum(int((p.evidence or {}).get("redactions") or 0) for p in parts),
                redaction_tier=tier,
                detected_company=company,
            )
            await self._audit(s, "document.parse", document_id, "ok")
            if status == "needs_account":
                _log.info(
                    "documents.needs_account",
                    extra={
                        "tenant_id": str(tenant_id),
                        "document_id": str(document_id),
                        "state": str(company.get("status")),
                        "reason": str(company.get("reason") or "no_match"),
                    },
                )
        return status

    async def _match(
        self, s: AsyncSession, doc: Any, company: dict[str, Any]
    ) -> tuple[str, UUID | None]:
        """Company match first (PR5), since the import and the index both depend on it: returns
        ``("proceed", account to bind or None)`` or ``("hold", None)``. A company counts only
        when the resolver says ``resolved`` and its score reaches ``bind_min_confidence``.
        Uploaded to an account: held when the file names ANOTHER company (the registrable domains
        differ); when the file names none or several, the account the user uploaded it to stands
        (logged ``documents.company_unconfirmed``) unless ``hold_unmatched_for_account`` is on (the
        strict rule: then it waits too). A workspace zip's tables carry no company evidence, so
        the strict rule would hold nearly every package uploaded on its own account page (PR5
        review, 2026-11-04). Uploaded without one: bind to the owner's account of that domain
        when there is one. Anything else waits as ``needs_account``. A document that was decided
        before (a re-read of an indexed one) is not asked again."""
        found = company.get("company") or {}
        domain = (
            registrable(str(found["domain"]))
            if company.get("status") == "resolved"
            and found.get("domain")
            and float(found.get("score") or 0) >= self.c.company.bind_min_confidence
            else None
        )
        if doc.account_id is not None:
            if doc.detected_company:
                return "proceed", None
            # a workspace package names its own company (2026-11-05): when that is another
            # domain than the account's, hold and ask; nothing is imported until the user says
            # whether the two are the same company (``same_company``) or picks the account
            workspace = await self._workspace_identity(s, doc)
            if workspace is not None:
                company["workspace"] = workspace
                if workspace.get("decision") == "mismatch":
                    company.update(
                        {
                            "status": "resolved",
                            "reason": "workspace_other_company",
                            "company": {
                                "name": workspace.get("name") or workspace.get("domain"),
                                "domain": workspace.get("domain"),
                                "score": 10.0,
                                "signals": {"workspace_record": 1},
                            },
                        }
                    )
                    return "hold", None
            own = await repo.account_domain(s, doc.account_id)
            if domain and own and registrable(own) != domain:
                if self.c.company.hold_mismatched_for_account:
                    return "hold", None  # the file is about another company
                return "proceed", None
            if domain is None:
                if self.c.company.hold_unmatched_for_account:
                    return "hold", None
                _log.info("documents.company_unconfirmed", extra={"state": company.get("status")})
            return "proceed", None
        if domain:
            account = await repo.account_for_domain(s, doc.id, domain)
            if account is not None:
                return "proceed", account
        return "hold", None

    async def _workspace_identity(self, s: AsyncSession, doc: Any) -> dict[str, Any] | None:
        """What a workspace package says about its company, compared with the account it was
        uploaded to (``documents.workspace_identity``): None when the upload is not a workspace
        package or names no company; else the identity with ``decision`` ``same`` or
        ``mismatch`` and the account's own domain and name, for the prompt."""
        from documents import workspace_identity as WI

        key = getattr(doc, "object_key", None)
        if not key:
            return None
        try:
            with self.blobs.open(str(key)) as reader:
                data = reader.read()
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                roles = ws.member_roles(archive.namelist())
            if not roles:
                return None
            members = WI.read_identity_members(data, roles)
        except Exception:  # noqa: BLE001 - not a zip, or unreadable: the resolver decides
            return None
        identity = WI.identity_of(members, roles)
        if not identity.found:
            return None
        account = await repo.account_identity(s, doc.account_id)
        if account is None:
            return None
        bound = WI.account_bound_domains(str(account.domain), account.watch_config_json)
        decision = WI.decide(identity, bound)
        if decision == "mismatch":
            _log.info(
                "documents.workspace_other_company",
                extra={
                    "document_id": str(doc.id),
                    "account_domain": str(account.domain),
                    "workspace_domain": identity.domain,
                },
            )
        return {
            **identity.as_dict(),
            "decision": decision,
            "account_domain": str(account.domain),
            "account_name": account.name,
        }

    async def same_company(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any]:
        """The user says the workspace's company and the account are the same company: bind the
        workspace's domain, name and careers hosts to the account (its watch config), record the
        decision on the document and release the held upload so the import runs. Only an upload
        held for ``workspace_other_company`` can be confirmed this way."""
        from documents import workspace_identity as WI

        async with self.txn(tenant_id) as s:
            doc = await repo.get(s, document_id, lock=True)
            if doc is None:
                raise DocumentError("not_found", "The document was not found.")
            company = dict(doc.detected_company or {})
            workspace = dict(company.get("workspace") or {})
            if (
                doc.status != "needs_account"
                or doc.account_id is None
                or workspace.get("decision") != "mismatch"
            ):
                raise DocumentError(
                    "not_held_for_company",
                    "This file is not waiting for a company confirmation.",
                )
            account = await repo.account_identity(s, doc.account_id)
            if account is None:
                raise DocumentError("not_found", "The account was not found.")
            identity = WI.WorkspaceIdentity(
                domain=str(workspace.get("domain") or ""),
                name=str(workspace.get("name") or ""),
                careers_hosts=tuple(str(h) for h in workspace.get("careers_hosts") or ()),
                aliases=tuple(str(a) for a in workspace.get("aliases") or ()),
            )
            await set_account_watch_config(
                s, doc.account_id, WI.bound_watch_config(account.watch_config_json, identity)
            )
            company["workspace"] = {**workspace, "decision": "same"}
            company["reason"] = None
            await repo.set_status(s, document_id, "needs_account", detected_company=company)
            released = await repo.release_hold(s, [document_id])
            await self._audit(s, "document.same_company", document_id, "ok")
        if released:
            await self.scheduler.enqueue_document(tenant_id, document_id)
        return await self.status(tenant_id, document_id)

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

    @_doc_logged
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
            lease = _LEASE.get()
            if lease is not None and lease[1].lost:
                return "busy"  # another call holds the document now: it writes, this one stops
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
                    owner=lease[0] if lease is not None else None,
                ):
                    return "noop"  # deleted, or its lease lost, while it was being embedded
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
        await self._analyse(tenant_id, row)
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

    @_doc_logged
    async def _analyse(self, tenant_id: UUID, row: Any) -> None:
        """Start the model analysis of an indexed document bound to an account (owner's rule
        2026-11-03); never fails the document."""
        if self.analysis is None or row.account_id is None:
            return
        try:
            await self.analysis(tenant_id, row.id)
        except Exception as error:  # noqa: BLE001 - the analysis never fails the document
            _log.warning(
                "documents.analysis_failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "document_id": str(row.id),
                    "error_class": type(error).__name__,
                },
            )

    @_doc_logged
    async def _import_deliverables(self, tenant_id: UUID, row: Any) -> None:
        """The rows of an uploaded deliverable workbook as job postings, people and technologies
        of the account (``documents.deliverables``); a failure is logged and never stops
        indexing (the passages are indexed either way)."""
        from documents.deliverables import import_document

        now = self.clock.now()
        try:
            counts = await import_document(
                self.txn, tenant_id, row, now=now, payloads=self.payloads, blobs=self.blobs
            )
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
            await self._record_import_failure(tenant_id, row, error)
            return  # not marked: the sweep's backfill reads it again (bounded)
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

    async def _record_import_failure(self, tenant_id: UUID, row: Any, error: Exception) -> None:
        """A failure the importer could not record itself (it raised before its run existed) is
        recorded as a failed run, so the import step is not tried again on every task and the
        bounded backfill counts it. Never raises."""
        from documents import import_status
        from documents.deliverables import error_code

        try:
            async with self.txn(tenant_id) as s:
                if await import_status.attempted(s, tenant_id, row.id):
                    return
            await import_status.record_loose(
                self.txn,
                tenant_id,
                document_id=row.id,
                account_id=row.account_id,
                owner=str(getattr(row, "uploaded_by", "") or "") or None,
                error=f"{type(error).__name__} ({error_code(error)})",
            )
        except Exception as status_error:  # noqa: BLE001 - never mask the import's own failure
            _log.warning(
                "documents.import_status_failed",
                extra={"error_class": type(status_error).__name__},
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
            # the same transaction: the document's total is the sum of its call-log rows
            await repo.add_embedding_tokens(s, tenant_id, document_id, tokens)

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
        """Bind documents to the account the user confirmed, then index the parsed ones. An upload
        held as ``needs_account`` is released, to this account even when it was held on another."""
        async with self.txn(tenant_id) as s:
            rows = await repo.many(s, document_ids)
            if len(rows) != len(set(document_ids)):
                raise DocumentError("not_found", "A document was not found.")
            if any(
                r.account_id not in (None, account_id) and r.status != "needs_account" for r in rows
            ):
                raise DocumentError(
                    "already_bound", "A document already belongs to another account."
                )
            await repo.bind_account(s, document_ids, account_id, rebind=True)
            released = set(await repo.release_hold(s, document_ids))
        for r in rows:
            if r.status == "parsed" or r.id in released:
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
            await self._drop_quarantined(row.quarantine_key)
        if row.object_key:
            # the raw object is shared by every document of the tenant with the same sha256
            # (0078): delete it, and its registry row, only when this was the last one
            shared = False
            if row.sha256:
                # every user of the tenant may hold a document with this hash (private
                # workspaces): the count and the registry row are read in SYSTEM scope, or the
                # caller's own view would say "last one" while a colleague's document still
                # points at the object (leakage review 2026-10-30, F6)
                from account_intelligence.db.system_scope import system_scope

                with system_scope():
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

    async def members(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any]:
        """The files inside an uploaded zip (``document_members``): path, role, what the read
        did with each and why, what it produced; empty for a file that is not an archive."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id)
            rows = await repo.members(s, document_id) if row is not None else []
        if row is None:
            raise DocumentError("not_found", "The document was not found.")
        out = [
            {
                "id": str(m.id),
                "member": str(m.member_path),
                "role": str(m.role),
                "declared_type": m.declared_type,
                "bytes": int(m.bytes) if m.bytes is not None else None,
                "status": str(m.status),
                "reason": (
                    SKIP_CHECKS.get(str(m.failure), str(m.failure))
                    if m.status == "skipped"
                    else part_message(m.failure)
                    if m.status == "failed"
                    else None
                ),
                "passages": int(m.passages or 0),
                "tables": int(m.tables or 0),
                "rows_imported": int(m.rows_imported or 0),
                "rows_merged": int(m.rows_merged or 0),
                "rows_skipped": int(m.rows_skipped or 0),
                "audience": str(m.audience or "client"),
            }
            for m in rows
        ]
        return {
            "document_id": str(document_id),
            "members": out,
            "read": sum(1 for m in out if m["status"] == "read"),
            "skipped": sum(1 for m in out if m["status"] == "skipped"),
            "failed": sum(1 for m in out if m["status"] == "failed"),
        }

    async def member_file(
        self, tenant_id: UUID, document_id: UUID, member_id: UUID
    ) -> tuple[str, bytes]:
        """One file inside an uploaded zip, as uploaded (owner 2026-11-05: the account page must
        open every file of the package, not only list it): its path and bytes. The whole zip is
        already the user's own download, so one member is no wider. A member the archive no
        longer holds is ``not_found``; one over ``MEMBER_FILE_MAX`` bytes is ``too_large``."""
        import io
        import zipfile

        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id)
            rows = await repo.members(s, document_id) if row is not None else []
        if row is None:
            raise DocumentError("not_found", "The document was not found.")
        member = next((m for m in rows if str(m.id) == str(member_id)), None)
        key = getattr(row, "object_key", None)
        if member is None or not key:
            raise DocumentError("not_found", "The file inside was not found.")
        path = str(member.member_path)

        def read() -> bytes:
            """The stored upload's bytes (run off the event loop)."""
            try:
                with self.blobs.open(str(key)) as reader:
                    data = reader.read()
            except BlobMissing:
                raise DocumentError(
                    "not_found", "The uploaded file is no longer in storage."
                ) from None
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                try:
                    info = archive.getinfo(path)
                except KeyError:
                    raise DocumentError("not_found", "The file inside was not found.") from None
                if info.file_size > MEMBER_FILE_MAX:
                    raise DocumentError(
                        "too_large", "This file inside is too large to open on its own."
                    )
                return archive.read(info)

        return path, await asyncio.to_thread(read)

    async def _record_failed_members(self, s: AsyncSession, doc: Any, parts: list[Any]) -> None:
        """One ``failed`` member row for every file of a failed part that has no row yet (a
        failed part's rows were cleared with it), with the part's reason."""
        async with self._local(doc.object_key) as path:
            listing = _zip_listing(path)
        if not listing:
            return
        roles = ws.member_roles([name for name, _ in listing])
        have = await repo.member_paths(s, doc.id)
        rows: list[dict[str, Any]] = []
        for p in parts:
            if p.status != "failed":
                continue
            cursor = dict(p.start_cursor or {})
            start = int(cursor.get("member", 0))
            end = cursor.get("end_member")
            span = listing[start : int(end)] if end is not None else listing[start:]
            for name, size in span:
                if name in have:
                    continue
                role = roles.get(name, ws.MemberRole.OTHER)
                rows.append(
                    {
                        "member_path": name,
                        "role": str(role),
                        "declared_type": by_name(name),
                        "bytes": size,
                        "status": "failed",
                        "failure": str(p.failure or "parse:failed"),
                    }
                )
        if rows:
            await repo.record_members(s, doc.id, doc.account_id, None, rows)

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
            "needs_account": "needs_account",
            "indexed": "indexed", "rejected": "failed", "failed": "failed",
            "scan_failed": "failed", "abandoned": "failed",
        }[row.status]  # fmt: skip
        # an upload that waited at "parsed" with no account before 0109 is a held upload too
        held = row.status == "needs_account" or (row.status == "parsed" and row.account_id is None)
        if held:
            stage = "needs_account"
        reason = row.reject_reason or (row.stop_detail if row.status == "failed" else None)
        if row.status == "scan_failed":
            message: str | None = (
                SCAN_GIVEN_UP
                if int(getattr(row, "scan_attempts", 0) or 0) >= SCAN_ATTEMPTS
                else MESSAGES["scan_unavailable"]
            )
        elif row.status == "abandoned":
            reason, message = "abandoned", ABANDONED
        elif row.reject_reason:
            message = MESSAGES.get(row.reject_reason)
        elif row.stop_detail == "parts_failed":
            message = MESSAGES["parts_failed"]
        elif row.status == "failed":
            message = "The file could not be read."
        else:
            message = MESSAGES["needs_account"] if held else None
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
            "reason": reason,
            "message": message,
            "passages": row.passages or (parts or {}).get("passages", 0),
            "parts_total": int(row.parts_total or 0),
            "parts_done": int((parts or {}).get("done", 0)),
            # passages indexed so far (parts number their passages from 1, so the sequence
            # indexed through is the count): the file list says "Indexing 3,200 of 22,380"
            "indexed_passages": int(row.indexed_through or 0)
            if row.status in ("indexing", "indexed")
            else 0,
            # a zip: how many of its files were read, and the ones that were not, by name
            "audience": str(getattr(row, "audience", None) or "client"),
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
            "needs_company": held,
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


async def retry_failed_scans(
    now: datetime, scheduler: Any, *, idle_s: float, limit: int = 50, tenant_id: UUID | None = None
) -> list[UUID]:
    """Files whose scan could not run (``scan_failed``) are scanned again, per client, once
    they have waited ``idle_s`` since the last attempt and have fewer than ``SCAN_ATTEMPTS``
    attempts; after the last attempt they stay ``scan_failed``. Each goes back to
    ``quarantined`` and its task is enqueued. ``tenant_id`` limits the pass to one client."""
    from account_intelligence.db.session import tenant_txn

    idle_before = now - timedelta(seconds=idle_s)
    requeued: list[tuple[UUID, UUID]] = []
    # LOOP(scan retry clients): cap=every client once per pass, `limit` documents each;
    # exit=last client
    for tenant in await _tenants(tenant_id):
        async with tenant_txn(tenant, system=True) as s:
            for document_id in await repo.requeue_scans(
                s, tenant, idle_before, SCAN_ATTEMPTS, limit
            ):
                requeued.append((tenant, document_id))
    for tenant, document_id in requeued:
        await scheduler.enqueue_document(tenant, document_id)
    return [d for _, d in requeued]


async def mark_abandoned(
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    now: datetime,
    *,
    service: DocumentService | None = None,
    tenant_id: UUID | None = None,
    limit: int = 500,
) -> int:
    """The daily job: ``awaiting_upload`` documents that began more than 24 hours ago become
    ``abandoned`` and whatever object they left in quarantine is deleted (the bucket's own
    one-day rule is the backstop). Per client, inside that client's transaction, every statement
    naming the client; ``tenant_id`` limits the pass to one client. ``sessions`` opens the
    cross-client session that lists the clients; ``service`` is the document service that holds
    the stores (without it only the rows change). Returns how many documents were marked."""
    from account_intelligence.db.session import tenant_txn

    cutoff = now - ABANDON_AFTER
    marked = 0
    # LOOP(abandon clients): cap=every client once per pass, `limit` documents each;
    # exit=last client
    for tenant in await _tenants(tenant_id, sessions):
        async with tenant_txn(tenant, system=True) as s:
            gone = await repo.abandon_stale(s, tenant, cutoff, limit)
        marked += len(gone)
        if service is not None:
            # LOOP(abandon objects): cap=`limit` per client; exit=documents exhausted
            for _, key in gone:
                await service._drop_quarantined(key)  # noqa: SLF001 - the service's own stores
    return marked


async def _tenants(
    only: UUID | None,
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
) -> list[UUID]:
    """The clients to visit: the one named, else every client (the operator overview lists them
    by identifier)."""
    if only is not None:
        return [only]
    from sqlalchemy import text

    if sessions is None:
        from account_intelligence.db.system_scope import untenanted_session

        sessions = untenanted_session
    async with sessions() as plain:
        rows = await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        return [UUID(str(r.tenant_id)) for r in rows]
