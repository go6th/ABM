"""Download everything (owner decision 2026-10-14, Phase 4B section 6): one archive of an account
with everything collected for the user who asks, built in the background.

What the archive holds:

* ``Deliverables/``: the account's current deliverable files;
* ``Tables/``: every deep-dive table whole, as CSV and as Excel (``replica.table_export``: the
  client-file rules on every cell; a shareable workbook that still fails its last check is left
  out and named in ``README.txt``; Excel sheets continue past 1,048,576 rows);
* ``Internal - your files/``: the user's own uploaded files of this account and the Internal Only
  file findings. Internal material never enters a shareable file: this archive is the user's own.

Raw provider responses are left out (owner decision default). The archive is written to object
storage in zip parts of about ``PART_BYTES`` (a part holds whole files, so one very large file
makes its part larger); nothing holds the archive, a table or a file in memory. A part is fetched
with a short-lived link: a random token (stored only as its hash) valid ``LINK_TTL_S``, renewed
each time the status is read, with range requests (``open_part``).

GUARANTEE(test_download_everything_builds_parts_with_every_table_and_a_short_lived_link)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PART_BYTES = 2 * 1024**3  # about 2 GiB per zip part
LINK_TTL_S = 3600
COPY = 1 << 20
TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


class LinkRefused(Exception):
    """The link's token is wrong or expired, or the part does not exist."""


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def part_key(tenant_id: UUID, job_id: UUID, n: int) -> str:
    """Where part ``n`` of an archive is stored (no name in the key)."""
    return f"tenants/{tenant_id}/exports/{job_id}/part-{n:03d}.zip"


async def start(s: Any, account_id: UUID, request_id: UUID) -> UUID:
    """A new archive job for the caller (a repeated request returns the first)."""
    found = await s.scalar(
        text("SELECT id FROM download_jobs WHERE request_id = :q"), {"q": request_id}
    )
    if found is not None:
        return UUID(str(found))
    return UUID(
        str(
            await s.scalar(
                text(
                    "INSERT INTO download_jobs (account_id, request_id) VALUES (:a, :q)"
                    " RETURNING id"
                ),
                {"a": account_id, "q": request_id},
            )
        )
    )


async def view(s: Any, job_id: UUID, now: datetime) -> dict[str, Any] | None:
    """The job's status; when ready, its parts with a fresh short-lived link each."""
    row = (
        await s.execute(
            text(
                "SELECT d.id, d.status, d.parts, d.files, d.total_bytes, d.failure, d.expires_at,"
                " a.domain FROM download_jobs d JOIN accounts a ON a.id = d.account_id"
                " WHERE d.id = :j"
            ),
            {"j": job_id},
        )
    ).first()
    if row is None:
        return None
    parts: list[dict[str, Any]] = []
    expires: datetime | None = None
    if row.status == "ready" and row.expires_at > now:
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=LINK_TTL_S)
        await s.execute(
            text("UPDATE download_jobs SET token_sha256 = :t, token_expires_at = :e WHERE id = :j"),
            {"t": _sha(token), "e": expires, "j": job_id},
        )
        parts = [
            {
                "n": int(p["n"]),
                "name": str(p["name"]),
                "bytes": int(p["bytes"]),
                "url": f"/v1/downloads/{job_id}/parts/{int(p['n'])}?token={token}",
            }
            for p in row.parts
        ]
    return {
        "job_id": str(row.id),
        "status": "expired" if row.status == "ready" and row.expires_at <= now else row.status,
        "domain": str(row.domain),
        "files": int(row.files),
        "total_bytes": int(row.total_bytes),
        "failure": row.failure,
        "parts": parts,
        "links_expire_at": expires.isoformat() if expires else None,
    }


async def open_part(s: Any, job_id: UUID, n: int, token: str, now: datetime) -> dict[str, Any]:
    """The stored part a link names, after its token is checked (``LinkRefused``)."""
    row = (
        await s.execute(
            text(
                "SELECT parts, token_sha256, token_expires_at, status FROM download_jobs"
                " WHERE id = :j"
            ),
            {"j": job_id},
        )
    ).first()
    if (
        row is None
        or row.status != "ready"
        or row.token_sha256 is None
        or row.token_expires_at is None
        or row.token_expires_at < now
        or not secrets.compare_digest(row.token_sha256, _sha(token))
    ):
        raise LinkRefused()
    part = next((p for p in row.parts if int(p["n"]) == n), None)
    if part is None:
        raise LinkRefused()
    return dict(part)


@dataclass
class Sources:
    """Where the archive's contents are read from and where its parts are written."""

    txn: TxnFactory
    payloads: Any  # deliverables and internal files (``PayloadStore``)
    blobs: Any  # uploaded files and the archive parts (``BlobStore``)
    part_bytes: int = PART_BYTES


async def build(tenant_id: UUID, job_id: UUID, src: Sources) -> str:
    """Build the archive (see the module docstring); the job's end status. Idempotent: a job that
    is not queued is left as it is."""
    async with src.txn(tenant_id) as s:
        job = (
            await s.execute(
                text(
                    "UPDATE download_jobs SET status = 'running', started_at = now()"
                    " WHERE id = :j AND status = 'queued' RETURNING account_id, owner_user_id"
                ),
                {"j": job_id},
            )
        ).first()
        if job is None:
            return "skipped"
        account_id, user_id = UUID(str(job.account_id)), str(job.owner_user_id)
        name = str(
            await s.scalar(
                text("SELECT coalesce(name, domain) FROM accounts WHERE id = :a"),
                {"a": account_id},
            )
        )
    work = Path(tempfile.mkdtemp(prefix="everything-"))
    writer = _Parts(tenant_id, job_id, name, work, src)
    try:
        notes = await _fill(tenant_id, account_id, user_id, name, writer, work, src)
        writer.add_bytes("README.txt", _readme(name, notes).encode("utf-8"))
        parts = await writer.close()
    except Exception as error:  # noqa: BLE001 - the job records why; nothing half-built stays
        await writer.discard()
        async with src.txn(tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE download_jobs SET status = 'failed', failure = :f,"
                    " finished_at = now() WHERE id = :j"
                ),
                {"j": job_id, "f": f"The archive could not be built ({type(error).__name__})."},
            )
        return "failed"
    finally:
        for left in work.glob("*"):
            left.unlink(missing_ok=True)
        work.rmdir()
    async with src.txn(tenant_id) as s:
        await s.execute(
            text(
                "UPDATE download_jobs SET status = 'ready', parts = CAST(:p AS jsonb),"
                " files = :f, total_bytes = :b, finished_at = now() WHERE id = :j"
            ),
            {
                "j": job_id,
                "p": json.dumps(parts),
                "f": writer.files,
                "b": sum(int(p["bytes"]) for p in parts),
            },
        )
    return "ready"


async def _fill(
    tenant_id: UUID,
    account_id: UUID,
    user_id: str,
    name: str,
    writer: _Parts,
    work: Path,
    src: Sources,
) -> list[str]:
    """Add every deliverable, table and file of the user's; what was left out, in words."""
    from account_intelligence.replica import sheets as SH
    from account_intelligence.replica import table_export as TX
    from account_intelligence.replica import workspace as W

    notes: list[str] = []
    now = datetime.now(UTC)
    async with src.txn(tenant_id) as s:
        deliverables = await W.deliverables(s, account_id)
        keys = {
            str(r.id): str(r.object_key)
            for r in await s.execute(
                text("SELECT id, object_key FROM deliverables WHERE id = ANY(:i)"),
                {"i": [UUID(d["id"]) for d in deliverables]},
            )
        }
        internal = (
            await s.execute(
                text(
                    "SELECT i.object_key, r.created_at FROM internal_deliverables i"
                    " JOIN account_runs r ON r.id = i.run_id WHERE r.account_id = :a"
                    " ORDER BY r.created_at"
                ),
                {"a": account_id},
            )
        ).all()
        documents = (
            await s.execute(
                text(
                    "SELECT name, object_key FROM documents WHERE account_id = :a"
                    " AND object_key IS NOT NULL ORDER BY created_at"
                ),
                {"a": account_id},
            )
        ).all()
    for d in deliverables:
        key = keys.get(d["id"])
        if key:
            writer.add_bytes(f"Deliverables/{d['filename']}", await src.payloads.get(key))
    for sheet in SH.SHEETS:
        q = SH.parse(sheet, None, None, None)
        title = q.sheet.title.replace("/", "-")
        csv_path = work / f"{sheet}.csv"
        async with src.txn(tenant_id) as s:
            with csv_path.open("wb") as out:
                async for chunk in TX.csv_chunks(q, TX.rows(s, q, account_id, user_id, now)):
                    out.write(chunk)
        writer.add_file(f"Tables/{title}.csv", csv_path)
        xlsx_path = work / f"{sheet}.xlsx"
        try:
            async with src.txn(tenant_id) as s:
                await TX.write_xlsx(xlsx_path, q, name, TX.rows(s, q, account_id, user_id, now))
            writer.add_file(f"Tables/{title}.xlsx", xlsx_path)
        except SH.ExportBlocked:
            notes.append(f"Tables/{title}.xlsx was held back by a safety check (the CSV is in).")
        csv_path.unlink(missing_ok=True)
        xlsx_path.unlink(missing_ok=True)
    folder = "Internal - your files"
    for n, row in enumerate(internal, start=1):
        stamp = row.created_at.strftime("%Y-%m-%d")
        writer.add_bytes(
            f"{folder}/File findings {stamp} ({n}).docx", await src.payloads.get(row.object_key)
        )
    for row in documents:
        try:
            reader = src.blobs.open(row.object_key)
        except Exception:  # noqa: BLE001 - a file removed meanwhile is named, not fatal
            notes.append(f"{folder}/{row.name} was no longer in storage.")
            continue
        with reader:
            writer.add_stream(f"{folder}/{row.name}", reader)
    return notes


def _readme(name: str, notes: list[str]) -> str:
    lines = [
        f"Everything collected for {name}.",
        "",
        "Deliverables: the current files of the account.",
        "Tables: every table whole, as CSV and as Excel.",
        "Internal - your files: your own uploaded files and what was found in them. Internal:"
        " do not share.",
        "Raw provider responses are not included.",
    ]
    if notes:
        lines += ["", *notes]
    return "\r\n".join(lines) + "\r\n"


class _Parts:
    """Zip parts on local disk, each sent to storage when it is full."""

    def __init__(self, tenant_id: UUID, job_id: UUID, name: str, work: Path, src: Sources):
        self.tenant_id, self.job_id, self.src, self.work = tenant_id, job_id, src, work
        stem = "".join(c if c.isalnum() else "-" for c in name).strip("-") or "account"
        self.stem = f"{stem}-everything"
        self.done: list[dict[str, Any]] = []
        self.pending: list[tuple[int, Path]] = []
        self.n = 0
        self.files = 0
        self.zip: zipfile.ZipFile | None = None
        self.path: Path | None = None

    def _current(self) -> zipfile.ZipFile:
        if (
            self.zip is not None
            and self.path is not None
            and self.path.stat().st_size >= (self.src.part_bytes)
        ):
            self._seal()
        if self.zip is None:
            self.n += 1
            self.path = self.work / f"part-{self.n:03d}.zip"
            self.zip = zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
        return self.zip

    def _seal(self) -> None:
        assert self.zip is not None and self.path is not None
        self.zip.close()
        self.pending.append((self.n, self.path))
        self.zip, self.path = None, None

    def add_bytes(self, arcname: str, data: bytes) -> None:
        """One small file."""
        self._current().writestr(arcname, data)
        self.files += 1

    def add_file(self, arcname: str, path: Path) -> None:
        """One file from local disk (streamed)."""
        self._current().write(path, arcname)
        self.files += 1

    def add_stream(self, arcname: str, reader: IO[bytes]) -> None:
        """One file from a reader, copied in blocks."""
        with self._current().open(arcname, "w", force_zip64=True) as out:
            # LOOP(archive copy): cap=the file's size / 1 MiB; exit=end of file
            while block := reader.read(COPY):
                out.write(block)
        self.files += 1

    async def close(self) -> list[dict[str, Any]]:
        """Seal the last part and send every part to storage; the parts' list."""
        if self.zip is not None:
            self._seal()
        total = len(self.pending)
        for n, path in self.pending:
            key = part_key(self.tenant_id, self.job_id, n)
            size = path.stat().st_size

            async def chunks(p: Path = path) -> AsyncIterator[bytes]:
                with p.open("rb") as handle:
                    # LOOP(archive part upload): cap=part size / 1 MiB; exit=end of part
                    while block := handle.read(COPY):
                        yield block

            await self.src.blobs.write_stream(key, chunks(), max_bytes=size)
            name = f"{self.stem}.zip" if total == 1 else f"{self.stem}-part-{n}-of-{total}.zip"
            self.done.append({"n": n, "key": key, "name": name, "bytes": size})
            os.unlink(path)
        self.pending = []
        return self.done

    async def discard(self) -> None:
        """Remove whatever was built (a failed job leaves nothing)."""
        if self.zip is not None:
            self.zip.close()
        for p in self.done:
            await self.src.blobs.delete(p["key"])
