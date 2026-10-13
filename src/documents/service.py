"""Uploaded documents from upload to index (input mode "file").

::

    start_upload -> PUT bytes (signed URL, or the API's token-checked content route)
      -> complete (size and sha256 checked; status quarantined; processing enqueued)
      -> process: type check from content -> malware scan -> promote out of quarantine
         -> parse in the sandbox -> company evidence (raw) -> redact -> chunk -> passages
         -> (bound to an account) embed -> index in the private ``chunks`` index

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
import hashlib
import os
import re
import secrets
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from documents import repo
from documents.chunking import Chunker, Passage
from documents.company import Resolver
from documents.config import InputModesConfig, load_config
from documents.embed import EmbeddingPort
from documents.redact import Redactor
from documents.sandbox import ParsedUnit, SandboxFailed, parse_in_sandbox
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
}


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
    """Where uploaded bytes wait for the scan (separate from accepted documents)."""
    return f"tenants/{tenant_id}/quarantine/{document_id}/raw"


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

    async def start_upload(
        self,
        tenant_id: UUID,
        user: str,
        name: str,
        size: int,
        mime: str | None,
        account_id: UUID | None,
    ) -> dict[str, Any]:
        """A document row and where to PUT its bytes (checked before any byte arrives)."""
        display = clean_name(name)
        kind = self.declared_type(display)
        if size <= 0:
            raise DocumentError("empty")
        if size > self.c.upload.max_file_bytes:
            raise DocumentError("too_large")
        doc_id = uuid4()
        token = secrets.token_urlsafe(32)
        expires = self.clock.now() + timedelta(minutes=self.c.upload.upload_ttl_minutes)
        qkey = quarantine_key(tenant_id, doc_id)
        async with self.txn(tenant_id) as s:
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
        signed = await self.blobs.signed_put_url(
            qkey, self.c.upload.upload_ttl_minutes * 60, content_type, size
        )
        url = signed or f"/v1/documents/{doc_id}/content?token={token}"
        return {
            "document_id": str(doc_id),
            "upload_url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_at": expires.isoformat(),
            "max_bytes": size,
            "direct": signed is not None,
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

    async def complete(self, tenant_id: UUID, document_id: UUID) -> dict[str, Any]:
        """Check what arrived (size and hash) and enqueue the scan. Nothing is parsed here."""
        async with self.txn(tenant_id) as s:
            row = await repo.get(s, document_id, lock=True)
            if row is None:
                raise DocumentError("not_found", "The document was not found.")
            if row.status != "awaiting_upload":
                return self._view(row)
            if not await self.blobs.exists(row.quarantine_key):
                raise DocumentError("not_uploaded")
            size, sha = await asyncio.to_thread(self._hash, row.quarantine_key)
            if size != row.declared_bytes:
                await repo.reject(s, document_id, "size_mismatch", None)
                await self.blobs.delete(row.quarantine_key)
            elif size > self.c.upload.max_file_bytes:
                await repo.reject(s, document_id, "too_large", None)
                await self.blobs.delete(row.quarantine_key)
            else:
                await repo.set_status(
                    s,
                    document_id,
                    "quarantined",
                    size_bytes=size,
                    sha256=sha,
                    upload_token_sha256=None,
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
        """Scan, parse and index one document (see the module docstring). Returns the status,
        ``busy`` when another worker holds it, ``noop`` when there is nothing to do."""
        claimable = (*ACTIVE, "parsed")
        async with self.txn(tenant_id) as s:
            row = await repo.claim(
                s, document_id, self.worker, self.c.parsing.lease_ttl_s, claimable
            )
            current = None if row is not None else await repo.get(s, document_id)
        if row is None:
            # another worker holds a document that still has work: retry later; a finished,
            # rejected or missing document (a duplicate task) has nothing to do
            return "busy" if current is not None and current.status in claimable else "noop"
        try:
            status = row.status
            if status in ("quarantined", "scanning"):
                status = await self._scan(tenant_id, row)
                if status != "parsing":
                    return status
                async with self.txn(tenant_id) as s:
                    row = await repo.get(s, document_id)
            if status == "parsing":
                status = await self._parse(tenant_id, row)
                async with self.txn(tenant_id) as s:
                    row = await repo.get(s, document_id)
            if status in ("parsed", "indexing") and row.account_id is not None:
                status = await self._index(tenant_id, row)
            return str(status)
        finally:
            async with self.txn(tenant_id) as s:
                await repo.release(s, document_id, self.worker)

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
        target = document_key(tenant_id, row.id)
        await self.blobs.move(row.quarantine_key, target)
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "parsing", object_key=target, detected_type=kind)
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

    async def _parse(self, tenant_id: UUID, row: Any) -> str:
        async with self.txn(tenant_id) as s:
            tier = await repo.policy_tier(s, self.c.redaction.default_tier)
            exclude = await repo.tenant_domains(s)
        redactor = Redactor(tier)
        chunker = Chunker(self.c.chunking, row.name, redactor, self.c.parsing.max_passages)
        resolver = Resolver(self.c.company, exclude)
        passages: list[Passage] = []
        pages: set[tuple[str, Any]] = set()
        try:
            async with self._local(row.object_key) as path:
                async for unit in self._units(path, row):
                    if unit.fatal and unit.check == "unsupported_type":
                        raise SandboxFailed("unsupported_type")
                    for key in ("page", "slide", "sheet"):
                        if key in unit.location:
                            pages.add((key, unit.location[key]))
                    if len(pages) > self.c.parsing.max_pages:
                        raise SandboxFailed("page_limit")
                    resolver.feed(unit)
                    passages.extend(chunker.passages([unit]))
        except SandboxFailed as failed:
            async with self.txn(tenant_id) as s:
                await repo.set_status(s, row.id, "failed", stop_detail=f"parse:{failed.code}")
            return "failed"
        if not passages:
            async with self.txn(tenant_id) as s:
                await repo.set_status(s, row.id, "failed", stop_detail="parse:empty")
            return "failed"
        entries = [
            (p.seq, ph, kind, orig) for p in passages for ph, kind, orig in redactor.present(p.text)
        ]
        company = resolver.decide()
        if chunker.truncated:
            company["truncated_at_passages"] = self.c.parsing.max_passages
        async with self.txn(tenant_id) as s:
            await repo.clear_passages(s, row.id)
            await repo.write_passages(s, row.id, row.account_id, passages, entries)
            await repo.set_status(
                s,
                row.id,
                "parsed",
                passages=len(passages),
                redactions=len(redactor.entries()),
                redaction_tier=tier,
                detected_company=company,
            )
            await self._audit(s, "document.parse", row.id, "ok")
        return "parsed"

    async def _units(self, path: str, row: Any) -> AsyncIterator[ParsedUnit]:
        async for unit in parse_in_sandbox(
            path,
            row.detected_type,
            row.name,
            timeout_s=self.c.sandbox.timeout_s,
            memory_mb=self.c.sandbox.memory_mb,
            cpu_s=self.c.sandbox.cpu_s,
            max_text_bytes=self.c.parsing.max_text_bytes,
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
        async with self.txn(tenant_id) as s:
            await repo.set_status(s, row.id, "indexing")
            rows = await repo.passages(s, row.id)
        vectors: list[list[float]] = []
        batch = self.c.embedding.batch
        for i in range(0, len(rows), batch):
            texts = [r.content for r in rows[i : i + batch]]
            vectors.extend(await self.embedder.embed(texts))
            await self._meter(tenant_id, row.id, texts)
        async with self.txn(tenant_id) as s:
            if not await repo.index_passages(
                s, row.id, row.account_id, rows, vectors, self.embedder.model
            ):
                return "noop"  # deleted while it was being embedded
            await repo.set_status(s, row.id, "indexed")
        return "indexed"

    async def _meter(self, tenant_id: UUID, document_id: UUID, texts: list[str]) -> None:
        """Every embedding batch is logged with its model and cost (the file model call log)."""
        from decimal import Decimal

        from research_files import repo as files_repo

        tokens = sum(int(len(t) / self.c.chunking.chars_per_token) + 1 for t in texts)
        key = hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()
        async with self.txn(tenant_id) as s:
            await files_repo.log_call(
                s,
                file_id=document_id,
                task="embed_passages",
                model=self.embedder.model,
                key=f"embed:{self.embedder.model}:{key}",
                served_from="model",
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
        if row is None:
            raise DocumentError("not_found", "The document was not found.")
        return self._view(row)

    async def list(self, tenant_id: UUID, account_id: UUID | None) -> list[dict[str, Any]]:
        """The tenant's documents (one account's when given)."""
        async with self.txn(tenant_id) as s:
            rows = await repo.list_for(s, account_id)
        return [self._view(r) for r in rows]

    @staticmethod
    def _view(row: Any) -> dict[str, Any]:
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
            else ("The file could not be read." if row.status == "failed" else None),
            "passages": row.passages,
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

    async with sessions() as s:
        rows = (
            await s.execute(
                text("SELECT tenant_id, document_id FROM documents_orphaned(:b) LIMIT 200"),
                {"b": now - timedelta(seconds=orphan_after_s)},
            )
        ).all()
    for r in rows:
        await scheduler.enqueue_document(UUID(str(r.tenant_id)), UUID(str(r.document_id)))
    return [UUID(str(r.document_id)) for r in rows]
