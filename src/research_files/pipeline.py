"""Processing one research file: lease, hash check, parse with write-ahead, audit, analytics,
the gated model step, and one terminal status (PLAN.md).

Order of work, each step skipped when the stored state says it is done (so a resume continues
exactly where the previous worker stopped):

1. take the file's lease (one worker per file; a dead worker's lease expires);
2. verify the stored object's SHA-256 against the upload's (mismatch: FAILED, nothing usable);
3. parse with the type's plugin: every unit is stored with its findings, the accumulator
   checkpoint and the next cursor in ONE transaction before the next unit is read;
4. end-of-parse audit and the analytics document (one transaction);
5. section summaries through the model router: estimated first; above the approval limit the
   file waits in AUDITED with the estimate until a person approves; every call cached by content
   hash and logged write-ahead;
6. terminal status: ANALYZED, or PARTIAL with the reason (unsupported type, OCR or transcription
   unavailable, a corrupt file, a model stop, an unexpected error) keeping everything done so far.
   FAILED only when nothing usable exists (the stored object is missing or does not match).

An interrupt (SIGTERM, SIGINT, a cancelled task) leaves the file PARSING and releases the lease;
a killed worker leaves an expiring lease that ``sweep_files`` turns into PARTIAL ``worker_lost``.
``POST /v1/files/{id}/resume`` continues from the last finished unit.

GUARANTEE(test_kill_mid_parse_then_resume_repeats_nothing_and_loses_nothing)
GUARANTEE(test_any_stop_ends_partial_with_its_reason)
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from research_files import repo
from research_files.analytics import Accumulator, build, final_findings
from research_files.blobstore import COPY_BUFFER, BlobMissing, BlobStore
from research_files.checks import Finding
from research_files.config import ResearchFilesConfig, load_config
from research_files.detect import family
from research_files.modelsvc import FileModels, ModelStop, parse_json_reply, section_items
from research_files.parsers.base import ParseContext, Unit
from research_files.parsers.registry import parser_for

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
Hook = Callable[[int], Awaitable[None]]
PROCESSABLE = ("UPLOADED", "PARSING")
DOC_KINDS = ("page", "slide", "section", "text")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Processor:
    """See the module docstring."""

    blobs: BlobStore
    txn: TxnFactory
    models: FileModels
    worker_id: str
    cfg: ResearchFilesConfig = field(default_factory=load_config)
    now: Callable[[], datetime] = _now
    after_unit: Hook | None = None  # fault injection for kill tests; None in production

    async def process(self, tenant_id: UUID, file_id: UUID) -> str:
        """Run or resume the file; the outcome word (``busy`` when another worker has it)."""
        ttl = self.cfg.parsing.lease_ttl_s
        async with self.txn(tenant_id) as s:
            row = await repo.acquire(s, file_id, self.worker_id, self.now(), ttl, PROCESSABLE)
        if row is None:
            return "busy"
        try:
            return await self._run(tenant_id, row)
        except repo.LeaseLost:
            return "lease_lost"
        except Exception as error:
            await self._stop_unexpected(tenant_id, file_id, error)
            raise
        except BaseException:
            # an interrupt: the file stays PARSING and resumable; the lease is given up so a
            # resume does not wait for it to expire
            try:
                async with self.txn(tenant_id) as s:
                    await repo.release(s, file_id, self.worker_id)
            except BaseException:  # noqa: BLE001 - the process may be going away
                pass
            raise

    # ---------------------------------------------------------------- steps

    async def _run(self, tenant_id: UUID, row: Any) -> str:
        file_id = UUID(str(row.id))
        cursor: dict[str, Any] = dict(row.cursor or {})
        async with self.txn(tenant_id) as s:
            acc = Accumulator(await repo.load_state(s, file_id), self.cfg)
        if not cursor.get("verified"):
            problem = await self._verify(row)
            if problem is not None:
                async with self.txn(tenant_id) as s:
                    await repo.insert_findings(s, file_id, 0, [problem])
                    await repo.finish(
                        s,
                        file_id,
                        status="FAILED",
                        reason=problem.check,
                        detail=problem.message,
                        release_owner=self.worker_id,
                    )
                return "failed"
            cursor["verified"] = True
            async with self.txn(tenant_id) as s:
                await repo.set_cursor(
                    s, file_id, self.worker_id, cursor, self.now(), self.cfg.parsing.lease_ttl_s
                )
        kind = str(row.detected_type or "unknown")
        parser = parser_for(kind)
        acc.meta.setdefault("type", kind)
        acc.meta.setdefault("family", family(kind))
        if parser is None:
            if "unsupported" not in acc.stops:
                acc.stops.append("unsupported")
            cursor["parse_done"] = True
        if not cursor.get("parse_done"):
            cursor = await self._parse(tenant_id, row, parser, cursor, acc)
        if not cursor.get("finalized"):
            await self._finalize(tenant_id, file_id, cursor, acc, unsupported=parser is None)
        return await self._model_step(tenant_id, file_id, acc)

    async def _verify(self, row: Any) -> Finding | None:
        """Stream the stored object and compare its hash (None when it matches)."""

        def digest() -> str | None:
            try:
                handle = self.blobs.open(str(row.object_key))
            except BlobMissing:
                return None
            h = hashlib.sha256()
            with handle:
                # LOOP(hash verify read): cap=the object's size in COPY_BUFFER reads; exit=end
                while block := handle.read(COPY_BUFFER):
                    h.update(block)
            return h.hexdigest()

        found = await asyncio.to_thread(digest)
        if found is None:
            return Finding(
                "error",
                "file_missing",
                {},
                "The stored file is missing; nothing can be read.",
                "Upload the file again.",
            )
        if found != row.sha256:
            return Finding(
                "error",
                "integrity_mismatch",
                {},
                "The stored file does not match the uploaded file's SHA-256; it was not read.",
                "Upload the file again.",
            )
        return None

    async def _parse(
        self, tenant_id: UUID, row: Any, parser: Any, cursor: dict[str, Any], acc: Accumulator
    ) -> dict[str, Any]:
        file_id = UUID(str(row.id))
        ctx = ParseContext(self.cfg, str(row.name))
        handle = await asyncio.to_thread(self.blobs.open, str(row.object_key))
        seq = int(row.units_done)
        try:
            units = parser.units(handle, dict(cursor.get("parser") or {}), ctx)
            # LOOP(file units): cap=the file's units; exit=parser done, fatal unit
            while True:
                unit: Unit | None = await asyncio.to_thread(next, units, None)
                if unit is None:
                    break
                found = await asyncio.to_thread(self._apply, acc, seq + 1, unit)
                content = self._indexed(acc, unit)
                done = bool(unit.cursor.get("done")) or unit.fatal
                cursor = {**cursor, "parser": unit.cursor, "parse_done": done}
                if unit.fatal:
                    reason = unit.findings[0].check if unit.findings else "corrupt_file"
                    if reason not in acc.stops:
                        acc.stops.append(reason)
                stop = unit.stats.get("stop")
                if stop and stop not in acc.stops:
                    acc.stops.append(str(stop))
                seq += 1
                async with self.txn(tenant_id) as s:
                    await repo.write_unit(
                        s,
                        file_id=file_id,
                        owner=self.worker_id,
                        seq=seq,
                        kind=unit.kind,
                        location=unit.location,
                        content=content,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        stats=_public_stats(unit),
                        findings=found,
                        cursor=cursor,
                        state=acc.to_json(),
                        now=self.now(),
                        ttl=self.cfg.parsing.lease_ttl_s,
                    )
                if self.after_unit is not None:
                    await self.after_unit(seq)
                if done:
                    break
        finally:
            await asyncio.to_thread(handle.close)
        cursor["parse_done"] = True
        return cursor

    def _apply(self, acc: Accumulator, seq: int, unit: Unit) -> list[Finding]:
        """The unit's own findings plus the accumulator's (CPU work; runs in a thread)."""
        found = acc.keep(unit.findings)
        if unit.rows is not None and unit.table is not None:
            found += acc.add_rows(
                unit.table, unit.header, unit.header_row, unit.rows, unit.location
            )
        elif unit.kind in DOC_KINDS:
            found += acc.add_text(
                seq, unit.kind, unit.heading, unit.text, unit.location, unit.stats
            )
        if unit.kind in ("image", "media"):
            acc.meta[str(unit.location.get("member") or "file")] = unit.stats.get("metadata", {})
        return found

    def _indexed(self, acc: Accumulator, unit: Unit) -> str:
        """The searchable text stored for the unit (excerpt only past the index cap)."""
        text = unit.text
        if acc.indexed + len(text) > self.cfg.parsing.max_indexed_text_bytes:
            lines = text.splitlines(keepends=True)[: self.cfg.parsing.excerpt_rows]
            text = "".join(lines)
        acc.indexed += len(text)
        return text

    async def _finalize(
        self,
        tenant_id: UUID,
        file_id: UUID,
        cursor: dict[str, Any],
        acc: Accumulator,
        *,
        unsupported: bool,
    ) -> None:
        found = final_findings(acc)
        if unsupported:
            found.append(
                Finding(
                    "info",
                    "unsupported_type",
                    {},
                    f"Files of type {acc.meta.get('type', 'unknown')} are stored but not parsed.",
                    "Convert the file to a supported type to get an audit and analytics.",
                )
            )
        analytics = build(acc)
        cursor = {**cursor, "finalized": True}
        async with self.txn(tenant_id) as s:
            await repo.insert_findings(s, file_id, 0, found)
            await repo.set_analytics(s, file_id, analytics)
            await repo.set_cursor(
                s, file_id, self.worker_id, cursor, self.now(), self.cfg.parsing.lease_ttl_s
            )

    async def _model_step(self, tenant_id: UUID, file_id: UUID, acc: Accumulator) -> str:
        async with self.txn(tenant_id) as s:
            row = await repo.get_file(s, file_id)
            sections_rows = await repo.chunks(s, file_id, DOC_KINDS)
        analytics: dict[str, Any] = dict(row.analytics or {})
        document = analytics.get("document")
        stops = list(analytics.get("stops") or acc.stops)
        if document and self.models.configured:
            texts = {int(r.seq): (str(r.content), str(r.content_hash)) for r in sections_rows}
            items = section_items(self.cfg, document["sections"], texts)
            if items:
                estimate = await self.models.estimate(tenant_id, items, bool(row.analysis_approved))
                analytics["estimate"] = estimate.to_json()
                if estimate.needs_approval:
                    async with self.txn(tenant_id) as s:
                        await repo.finish(
                            s,
                            file_id,
                            status="AUDITED",
                            reason="approval_required",
                            analytics=analytics,
                            estimate=estimate.to_json(),
                            release_owner=self.worker_id,
                        )
                    return "awaiting_approval"
                summaries: dict[int, list[str]] = {}
                try:
                    for item in items:
                        reply, _ = await self.models.call(tenant_id, file_id, item)
                        parsed = parse_json_reply(reply)
                        summary = str(parsed.get("summary", "")) if parsed else reply.strip()
                        summaries.setdefault(int(item.payload["seq"]), []).append(summary[:1500])
                except ModelStop as stopped:
                    _merge(document, summaries)
                    async with self.txn(tenant_id) as s:
                        await repo.finish(
                            s,
                            file_id,
                            status="PARTIAL",
                            reason=stopped.reason,
                            detail=stopped.detail,
                            analytics=analytics,
                            release_owner=self.worker_id,
                        )
                    return "partial"
                _merge(document, summaries)
        analytics["model"] = "used" if self.models.configured else "not_configured"
        status, reason = ("PARTIAL", stops[0]) if stops else ("ANALYZED", None)
        async with self.txn(tenant_id) as s:
            await repo.finish(
                s,
                file_id,
                status=status,
                reason=reason,
                analytics=analytics,
                release_owner=self.worker_id,
            )
        return status.lower()

    async def _stop_unexpected(self, tenant_id: UUID, file_id: UUID, error: Exception) -> None:
        """PARTIAL with the error class and a redacted detail; FAILED only with nothing stored."""
        from account_intelligence.runs.stop import describe

        try:
            async with self.txn(tenant_id) as s:
                row = await repo.get_file(s, file_id)
                usable = row is not None and (row.units_done > 0 or row.analytics is not None)
                await repo.finish(
                    s,
                    file_id,
                    status="PARTIAL" if usable else "FAILED",
                    reason=type(error).__name__,
                    detail=describe(error),
                    release_owner=self.worker_id,
                )
        except Exception:  # noqa: BLE001 - the original error is what matters
            return


def _merge(document: dict[str, Any], summaries: dict[int, list[str]]) -> None:
    for section in document.get("sections", []):
        parts = summaries.get(int(section["seq"]))
        if parts:
            section["summary"] = " ".join(p for p in parts if p).strip()[:3000]
            section["summary_source"] = "model"


def _public_stats(unit: Unit) -> dict[str, Any]:
    keep = {k: v for k, v in unit.stats.items() if k in ("words", "empty", "part", "scanned")}
    if unit.rows is not None:
        keep["rows"] = len(unit.rows)
    return keep


async def sweep_files(
    sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    txn: TxnFactory,
    now: datetime,
    *,
    orphan_after_s: int,
) -> list[UUID]:
    """Files stuck in PARSING whose worker died end PARTIAL ``worker_lost`` (data kept)."""
    async with sessions() as s:
        rows = (
            await s.execute(
                text("SELECT tenant_id, file_id FROM research_files_orphaned(:b) LIMIT 200"),
                {"b": now - timedelta(seconds=orphan_after_s)},
            )
        ).all()
    ended = []
    for r in rows:
        async with txn(UUID(str(r.tenant_id))) as s:
            updated = await s.execute(
                text(
                    "UPDATE research_files SET status = 'PARTIAL', stop_reason = 'worker_lost',"
                    " stop_detail = 'worker_lost: the worker stopped and its lease expired; resume"
                    " continues from the last stored unit', lease_owner = NULL,"
                    " lease_expires_at = NULL, updated_at = now() WHERE id = :f"
                    " AND status = 'PARSING' AND COALESCE(lease_expires_at, updated_at) < :b"
                ),
                {"f": r.file_id, "b": now - timedelta(seconds=orphan_after_s)},
            )
        if updated.rowcount:  # type: ignore[attr-defined]
            ended.append(UUID(str(r.file_id)))
    return ended
