"""Download everything (owner decision 2026-10-14, Phase 4B section 6): one archive of an account
with everything collected for the user who asks, built in the background.

What the archive holds:

* ``Deliverables/``: the account's current deliverable files;
* ``Tables/``: every table of the account, repaired by the workspace repair stage
  (``exports.repair``, spec ``docs/repair/workspace_repair_spec.md``) and written as CSV and Excel:
  de-duplicated on a stable key, entity tagged, dated from the source (the collection time is its
  own column), every cell filled or marked, the client-file rules on every cell (a workbook a
  safety check would hold back has the offending cell fixed, never dropped to CSV only);
* ``Logs/``: the repair report (before and after, acceptance checks, what is still Not done and
  what the user must enable) and the log of every dropped row;
* ``Internal - your files/``: the user's own uploaded files of this account and the Internal Only
  file findings. Internal material never enters a shareable file: this archive is the user's own.

Raw provider responses are left out (owner decision default). The archive is written to object
storage in zip parts of about ``PART_BYTES`` (a part holds whole files, so one very large file
makes its part larger); nothing holds the archive or a file in memory, and the repair stage holds
one workspace's tables (tens of megabytes: posting texts are the bulk) while it builds them, in a
worker thread. Compressing and
copying into a part run in a worker thread, one file at a time: the archive is CPU and disk work,
and on the event loop it stalled every other request of the worker, its liveness probe included,
until Cloud Run killed the instance and a research slice on it (staging, 2026-10-20). A part is
fetched with a short-lived link: a random token (stored only as its hash) valid ``LINK_TTL_S``,
renewed each time the status is read, with range requests (``open_part``).

A build that dies with its instance never stays "running" (staging, 2026-10-20: the retry found the
job running, skipped it with 200 and the page waited for ever). While a build runs it beats every
``BEAT_S`` (``started_at`` moves forward); a task that finds a running job with a fresh beat answers
"busy" so the queue retries it, and one whose beat is older than ``STALE_S`` takes the job over and
builds it again. A job nobody builds any more is ended failed when its status is read
(``ABANDONED_S``), so the page offers Try again instead of waiting.

GUARANTEE(test_download_everything_builds_parts_with_every_table_and_a_short_lived_link)
"""

from __future__ import annotations

import asyncio
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
BEAT_S = 30  # a running build moves ``started_at`` forward this often
STALE_S = 120  # a running build silent this long has died: a retry takes it over
ABANDONED_S = 900  # queued or running this long with no beat: nobody builds it any more
STOPPED = "The archive build stopped before it finished. Try again."
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
    """The job's status; when ready, its parts with a fresh short-lived link each. A job nobody
    builds any more (no beat for ``ABANDONED_S``) is ended failed here."""
    await s.execute(
        text(
            "UPDATE download_jobs SET status = 'failed', failure = :f, finished_at = :now"
            " WHERE id = :j AND status IN ('queued', 'running')"
            " AND coalesce(started_at, created_at) < :cut"
        ),
        {"j": job_id, "f": STOPPED, "now": now, "cut": now - timedelta(seconds=ABANDONED_S)},
    )
    row = (
        await s.execute(
            text(
                "SELECT d.id, d.status, d.parts, d.files, d.total_bytes, d.failure, d.expires_at,"
                " a.domain FROM download_jobs d JOIN accounts a ON a.id = d.account_id"
                " WHERE d.id = :j AND a.deleted_at IS NULL"
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
    """Build the archive (see the module docstring); the job's end status. A ready or failed job
    is left as it is ("skipped"); a running one with a fresh beat is "busy" (the task asks the
    queue to retry); a running one whose beat is older than ``STALE_S`` is taken over."""
    async with src.txn(tenant_id) as s:
        job = (
            await s.execute(
                text(
                    "UPDATE download_jobs SET status = 'running', started_at = now()"
                    " WHERE id = :j AND (status = 'queued' OR (status = 'running'"
                    " AND started_at < now() - make_interval(secs => :stale)))"
                    " RETURNING account_id, owner_user_id"
                ),
                {"j": job_id, "stale": STALE_S},
            )
        ).first()
        if job is None:
            state = await s.scalar(
                text("SELECT status FROM download_jobs WHERE id = :j"), {"j": job_id}
            )
            return "busy" if state == "running" else "skipped"
        account_id, user_id = UUID(str(job.account_id)), str(job.owner_user_id)
        name = str(
            await s.scalar(
                text("SELECT coalesce(name, domain) FROM accounts WHERE id = :a"),
                {"a": account_id},
            )
        )
    work = Path(tempfile.mkdtemp(prefix="everything-"))
    writer = _Parts(tenant_id, job_id, name, work, src)
    beat = asyncio.create_task(_beat(tenant_id, job_id, src))
    try:
        profile, notes = await _fill(tenant_id, account_id, user_id, name, writer, work, src)
        # the README is written from the members that exist and refused when it lists a path
        # that is not in the archive (workspace repair, 2026-10-30)
        from exports.repair.package import clean_text, finish_readme

        readme = clean_text(finish_readme(profile, writer.names, notes))
        await writer.add_bytes("README.txt", readme.encode("utf-8"))
        parts = await writer.close()
    except Exception as error:  # noqa: BLE001 - the job records why; nothing half-built stays
        beat.cancel()
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
        beat.cancel()
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


async def _beat(tenant_id: UUID, job_id: UUID, src: Sources) -> None:
    """Move the running job's ``started_at`` forward every ``BEAT_S`` until cancelled."""
    # LOOP(archive heartbeat): cap=the build's length / BEAT_S; exit=cancelled when the build ends
    while True:
        await asyncio.sleep(BEAT_S)
        try:
            async with src.txn(tenant_id) as s:
                await s.execute(
                    text(
                        "UPDATE download_jobs SET started_at = now()"
                        " WHERE id = :j AND status = 'running'"
                    ),
                    {"j": job_id},
                )
        except Exception:  # noqa: BLE001 - a missed beat is retried at the next one
            continue


async def _fill(
    tenant_id: UUID,
    account_id: UUID,
    user_id: str,
    name: str,
    writer: _Parts,
    work: Path,
    src: Sources,
) -> tuple[Any, list[str]]:
    """Add every deliverable, repaired table and file of the user's. Returns the workspace
    profile and what was left out or is not done, in words (the README prints them).

    The tables are not the screens' live queries: they are the repair stage's tables
    (``exports.repair``), built for this workspace from its raw pulls, de-duplicated, entity
    tagged, dated from the source, filled or marked, and checked (acceptance checks, repair
    report and exclusions log in ``Logs/``). The stage reads everything about the workspace from
    its own data (``exports.repair.profile``), so every account gets the same treatment."""
    import json

    from account_intelligence.replica import repair_guard
    from account_intelligence.replica import sheets as SH
    from account_intelligence.replica import workspace as W
    from agents.config import load_skill_config
    from exports.repair import package as PK
    from exports.repair import pipeline as RP
    from exports.repair import readers as RD
    from exports.repair.profile import load_profile

    repair_guard.install()  # the client-file rules the packager works through
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
        profile = await load_profile(s, account_id, user_id, now, load_skill_config().scope)
        inp = await RD.load_input(
            s,
            profile,
            account_id,
            user_id,
            payload=src.payloads.get,
            tracker_labels=json.loads(SH.tracker_labels()),
        )
    for d in deliverables:
        key = keys.get(d["id"])
        if key:
            body = await src.payloads.get(key)
            inp.deliverables[d["filename"]] = body
            await writer.add_bytes(f"Deliverables/{d['filename']}", body)
    folder = "Internal - your files"
    for n, row in enumerate(internal, start=1):
        stamp = row.created_at.strftime("%Y-%m-%d")
        await writer.add_bytes(
            f"{folder}/File findings {stamp} ({n}).docx", await src.payloads.get(row.object_key)
        )
    # the repair stage is CPU work (dedupe, workbooks, the safety scan): off the event loop, as
    # the archive's other CPU work is (a stalled loop killed the worker on 2026-10-20)
    result = await asyncio.to_thread(RP.repair, profile, inp)
    members, table_notes = await asyncio.to_thread(
        PK.build_members,
        profile,
        result.tables,
        deliverables={},
        report_md=result.report_md,
        reserved=[f"{folder}/{row.name}" for row in documents],
    )
    for arcname in sorted(members):
        await writer.add_bytes(arcname, members[arcname])
    notes += table_notes
    failed = [c.name for c in result.checks if not c.passed]
    if failed:
        notes.append(
            "Acceptance checks that did not pass: "
            + ", ".join(failed)
            + " (Logs/repair_report.md has the numbers)."
        )
    for row in documents:
        try:
            reader = src.blobs.open(row.object_key)
        except Exception:  # noqa: BLE001 - a file removed meanwhile is named, not fatal
            notes.append(f"{folder}/{row.name} was no longer in storage.")
            continue
        with reader:
            await writer.add_stream(f"{folder}/{row.name}", reader)
    return profile, notes


async def _yielding(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """The rows, handing the event loop a turn after each: every cell is checked for client-file
    safety on the loop, so a table of many thousand rows would otherwise hold it between two
    database pages."""
    # LOOP(archive table rows): cap=the table's rows; exit=end of the table
    async for row in source:
        yield row
        await asyncio.sleep(0)


class _Parts:
    """Zip parts on local disk, each sent to storage when it is full. Every write runs in a worker
    thread (``asyncio.to_thread``) and is awaited before the next, so one thread touches the zip at
    a time."""

    def __init__(self, tenant_id: UUID, job_id: UUID, name: str, work: Path, src: Sources):
        self.tenant_id, self.job_id, self.src, self.work = tenant_id, job_id, src, work
        stem = "".join(c if c.isalnum() else "-" for c in name).strip("-") or "account"
        self.stem = f"{stem}-everything"
        self.done: list[dict[str, Any]] = []
        self.pending: list[tuple[int, Path]] = []
        self.n = 0
        self.files = 0
        self.names: list[str] = []  # every member written (the README is checked against them)
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

    async def add_bytes(self, arcname: str, data: bytes) -> None:
        """One small file."""
        await asyncio.to_thread(lambda: self._current().writestr(arcname, data))
        self.files += 1
        self.names.append(arcname)

    async def add_file(self, arcname: str, path: Path) -> None:
        """One file from local disk (streamed)."""
        await asyncio.to_thread(lambda: self._current().write(path, arcname))
        self.files += 1
        self.names.append(arcname)

    async def add_stream(self, arcname: str, reader: IO[bytes]) -> None:
        """One file from a reader, copied in blocks."""
        await asyncio.to_thread(self._copy, arcname, reader)
        self.files += 1
        self.names.append(arcname)

    def _copy(self, arcname: str, reader: IO[bytes]) -> None:
        with self._current().open(arcname, "w", force_zip64=True) as out:
            # LOOP(archive copy): cap=the file's size / 1 MiB; exit=end of file
            while block := reader.read(COPY):
                out.write(block)

    async def close(self) -> list[dict[str, Any]]:
        """Seal the last part and send every part to storage; the parts' list."""
        if self.zip is not None:
            await asyncio.to_thread(self._seal)
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
            await asyncio.to_thread(self.zip.close)
        for p in self.done:
            await self.src.blobs.delete(p["key"])
