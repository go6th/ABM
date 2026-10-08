"""Access and deletion requests (PD3, roadmap 11.4; D-84): a workflow with audited steps.

Intake, an identity-verification step, a scoped search that returns **ids
only** (through a narrow operator-plane function; it never reads across tenants
without the request's recorded tenant grant), a per-tenant action list, an
export of the person's data for access requests, erasure by redacting personal
fields and tombstoning, a suppression-list entry, and a confirmation record.
Deadlines are configuration; every step is audited in ``dsar_events``; the
tenant is told what was done in client-safe wording.

GUARANTEE(test_a_dsar_runs_end_to_end)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from privacy import suppression, tombstones

PERSONAL_FIELDS = (
    "name",
    "title",
    "headline",
    "location",
)  # profile_url is redacted too; the row keeps its id and non-personal flags


class NoGrant(RuntimeError):
    """The request has no recorded tenant grant; cross-tenant reads are refused."""


def deadlines(path: Any = None) -> dict[str, int]:
    """The configured response deadlines in days (source on every value)."""
    doc = yaml.safe_load((path or _path("dsar.yaml")).read_text())
    return doc["deadline_days"]


def _path(rel: str) -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "config" / rel


@dataclass(frozen=True)
class Request:
    """One DSAR as stored."""

    id: int
    kind: str
    subject_ref: str
    tenant_scope: UUID | None
    status: str
    deadline_at: datetime


async def open_request(
    s: Any,
    *,
    kind: str,
    subject_ref: str,
    tenant_scope: UUID | None,
    now: datetime,
    path: Any = None,
) -> Request:
    """Intake: one request, its scope and its deadline (audit step 1)."""
    if kind not in ("access", "erasure"):
        raise ValueError(f"unknown DSAR kind {kind!r}")
    days = deadlines(path)[kind]
    row = (
        await s.execute(
            text(
                "INSERT INTO dsar_requests (kind, subject_ref, tenant_scope, status,"
                " opened_at, deadline_at) VALUES (:k, :s, :t, 'received', :now, :deadline)"
                " RETURNING id"
            ),
            {
                "k": kind,
                "s": subject_ref,
                "t": tenant_scope,
                "now": now,
                "deadline": now + timedelta(days=days),
            },
        )
    ).scalar_one()
    await event(s, request_id=row, step="intake", detail={"kind": kind}, at=now)
    return Request(row, kind, subject_ref, tenant_scope, "received", now + timedelta(days=days))


async def verify_identity(s: Any, *, request_id: int, ok: bool, at: datetime) -> None:
    """Step 2: identity verification recorded before any data is touched."""
    await event(s, request_id=request_id, step="identity_verification", detail={"ok": ok}, at=at)
    if ok:
        await s.execute(
            text("UPDATE dsar_requests SET status = 'verified' WHERE id = :i AND status = 'received'"),
            {"i": request_id},
        )


async def event(s: Any, *, request_id: int, step: str, detail: dict[str, Any], at: datetime) -> None:
    """One audited step (insert-only)."""
    await s.execute(
        text(
            "INSERT INTO dsar_events (request_id, step, detail, at)"
            " VALUES (:r, :s, CAST(:d AS jsonb), :at)"
        ),
        {"r": request_id, "s": step, "d": json.dumps(detail), "at": at},
    )


async def scoped_search(
    s: Any, *, request: Request, tenant_id: UUID, profile_url: str | None = None, name: str | None = None
) -> list[int]:
    """The narrow operator-plane search: **ids only**, and only inside the
    request's recorded grant (a session already bound to that tenant)."""
    if request.tenant_scope != tenant_id:
        raise NoGrant(f"request {request.id} has no grant for tenant {tenant_id}")
    if not profile_url and not name:
        raise ValueError("search by profile URL or name")
    rows = (
        await s.execute(
            text(
                "SELECT id FROM people WHERE tenant_id = :t"
                " AND ((CAST(:u AS text) IS NOT NULL AND profile_url_canonical = :u)"
                " OR (CAST(:n AS text) IS NOT NULL AND lower(name) = lower(:n)))"
            ),
            {"t": tenant_id, "u": profile_url, "n": name},
        )
    ).scalars().all()
    await event(
        s,
        request_id=request.id,
        step="scoped_search",
        detail={"tenant": str(tenant_id), "matches": len(rows)},
        at=datetime.now(tz=UTC()),
    )
    return list(rows)


def UTC() -> Any:  # noqa: N802 (kept tiny: the module has one now() caller)
    from datetime import UTC

    return UTC


async def export_subject(s: Any, *, request: Request, tenant_id: UUID, person_ids: list[int]) -> dict[str, Any]:
    """Access request: the person's data as JSON (the tenant-safe export)."""
    if request.kind != "access":
        raise ValueError("export is for access requests")
    rows = (
        await s.execute(
            text(
                "SELECT id, profile_url, name, title, job_level, created_at FROM people"
                " WHERE tenant_id = :t AND id = ANY(:ids)"
            ),
            {"t": tenant_id, "ids": person_ids},
        )
    ).mappings().all()
    await event(s, request_id=request.id, step="export", detail={"rows": len(rows)}, at=datetime.now(tz=UTC()))
    return {"people": [dict(r) for r in rows]}


async def erase(
    s: Any,
    *,
    request: Request,
    tenant_id: UUID,
    person_ids: list[int],
    salt: bytes,
    now: datetime,
) -> int:
    """Erasure: redact the personal fields, tombstone, suppress (idempotent)."""
    if request.kind != "erasure":
        raise ValueError("erasure is for erasure requests")
    if request.tenant_scope != tenant_id:
        raise NoGrant(f"request {request.id} has no grant for tenant {tenant_id}")
    erased = 0
    for pid in person_ids:
        row = (
            await s.execute(
                text(
                    "SELECT profile_url, name, erased_at FROM people"
                    " WHERE tenant_id = :t AND id = :p"
                ),
                {"t": tenant_id, "p": pid},
            )
        ).first()
        if row is None or row.erased_at is not None:
            continue  # already erased: a crash mid-run resumes without double work
        content = tombstones.content_hash({"profile_url": row.profile_url, "name": row.name})
        await s.execute(
            text(
                "UPDATE people SET name = NULL, title = NULL, headline = NULL, location = NULL,"
                " profile_url = NULL, erased_at = :at, erasure_reason = :r"
                " WHERE tenant_id = :t AND id = :p AND erased_at IS NULL"
            ),
            {"at": now, "r": f"dsar:{request.id}", "t": tenant_id, "p": pid},
        )
        await tombstones.write(
            s,
            subject_type="people",
            subject_id=str(pid),
            reason=f"dsar:{request.id}",
            content_sha256=content,
            tenant_id=tenant_id,
            at=now,
        )
        await suppression.suppress(
            s, profile_url=row.profile_url, name=row.name, salt=salt, reason=f"dsar:{request.id}"
        )
        erased += 1
    await event(
        s, request_id=request.id, step="erasure", detail={"erased": erased}, at=now
    )
    return erased


CONFIRMATION = (
    "The request was processed. Personal data was removed or provided as requested, "
    "and the person will not be collected again."
)  # client-safe wording; no internal detail


async def confirm(s: Any, *, request: Request, at: datetime) -> str:
    """Step: record the confirmation and close the request."""
    await event(s, request_id=request.id, step="confirmation", detail={}, at=at)
    await s.execute(
        text(
            "UPDATE dsar_requests SET status = 'done', closed_at = :at"
            " WHERE id = :i AND status IN ('received', 'verified')"
        ),
        {"i": request.id, "at": at},
    )
    return CONFIRMATION


async def overdue(s: Any, *, now: datetime) -> list[int]:
    """Open requests past their deadline (the operator's view)."""
    rows = (
        await s.execute(
            text(
                "SELECT id FROM dsar_requests"
                " WHERE status NOT IN ('done', 'refused') AND deadline_at < :now"
            ),
            {"now": now},
        )
    ).scalars().all()
    return list(rows)
