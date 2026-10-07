"""The write-back flow (CW1 to CW5, D-77): opt-in, mapped, previewed, approved, idempotent,
reconcilable, create-only.

* ``enable`` and ``revoke`` per CRM (insert-only events); revoking stops writes at once (checked
  before every create).
* ``confirm_mapping``: a human confirms the CRM record of an account; ``resolve`` refuses an
  ambiguous or unconfirmed match (writes nothing).
* ``preview``: the exact draft that would be created (rendered, linted, scanned), nothing stored.
* ``request``: an insert-only write intent, unique per (account, crm, kind, content hash): a
  replay returns the existing write.
* ``approve``: per-batch human approval (the default).
* ``execute``: ONE create attempt per write: the ``sending`` event is committed first, the CRM is
  called with no transaction open, then ``created`` is recorded. A crash between the create and
  the record leaves ``sending`` without an outcome; ``reconcile`` resolves it by a read-only marker
  search: found is ``adopted``, not found is ``failed`` (no blind retry).

Nothing here can send an email, a message or a sequence; tasks carry preparation wording.
The one writer of ``crm_connection_events``, ``crm_mappings``, ``crm_writes`` and
``crm_write_events``.

GUARANTEE(test_the_write_back_flow_is_opt_in_mapped_approved_and_idempotent)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from crm_writeback import mapping, payloads
from crm_writeback.client import CrmClient, Draft, Kind
from obs.setup import record

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[Any]]


class WriteRefused(PermissionError):
    """A write that must not happen; fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@lru_cache
def config() -> dict[str, Any]:
    """``config/crm.yaml``."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "crm.yaml").read_text())
    return data


async def _event(s: Any, crm: str, event: str, actor: str, now: datetime) -> None:
    await s.execute(
        text("INSERT INTO crm_connection_events (crm, event, actor, at) VALUES (:c, :e, :a, :now)"),
        {"c": crm, "e": event, "a": actor, "now": now},
    )


async def enable(s: Any, crm: str, *, actor: str, now: datetime) -> None:
    """Opt in (tenant admin)."""
    await _event(s, crm, "enabled", actor, now)


async def revoke(s: Any, crm: str, *, actor: str, now: datetime) -> None:
    """Stop all writes for this CRM at once."""
    await _event(s, crm, "revoked", actor, now)


async def enabled(s: Any, crm: str) -> bool:
    """The newest connection event is ``enabled`` (off by default)."""
    row = (
        await s.execute(
            text("SELECT event FROM crm_connection_events WHERE crm = :c ORDER BY id DESC LIMIT 1"),
            {"c": crm},
        )
    ).first()
    return row is not None and row.event == "enabled"


async def confirm_mapping(
    s: Any, *, account_id: UUID, crm: str, record_id: str, actor: str, now: datetime
) -> None:
    """A human-confirmed account mapping (a new version)."""
    v = int(
        (
            await s.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM crm_mappings WHERE account_id = :a"
                    " AND crm = :c"
                ),
                {"a": account_id, "c": crm},
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO crm_mappings (account_id, crm, version, record_id, confirmed_by,"
            " created_at)"
            " VALUES (:a, :c, :v, :r, :by, :now)"
        ),
        {"a": account_id, "c": crm, "v": v, "r": record_id, "by": actor, "now": now},
    )


async def confirmed_record(s: Any, account_id: UUID, crm: str) -> str | None:
    """The newest confirmed mapping."""
    row = (
        await s.execute(
            text(
                "SELECT record_id FROM crm_mappings WHERE account_id = :a AND crm = :c"
                " ORDER BY version DESC LIMIT 1"
            ),
            {"a": account_id, "c": crm},
        )
    ).first()
    return None if row is None else str(row.record_id)


def preview(
    kind: Kind,
    *,
    candidates: list[str],
    confirmed: str | None,
    account_label: str,
    items: list[payloads.Item],
) -> Draft:
    """The exact draft, or WriteRefused (pure; nothing stored)."""
    res, record = mapping.resolve(candidates, confirmed)
    if res != "mapped" or record is None:
        raise WriteRefused(f"mapping_{res}")
    lim = config()["limits"]
    try:
        return payloads.render(
            kind,
            account_record_id=record,
            account_label=account_label,
            items=items,
            marker_prefix=config()["marker_prefix"],
            title_chars=int(lim["title_chars"]),
            body_chars=int(lim["body_chars"]),
        )
    except payloads.PayloadRefused as e:
        raise WriteRefused(e.reason) from None


@dataclass(frozen=True)
class Requested:
    """A write intent."""

    write_id: UUID
    new: bool


async def request(
    s: Any, *, account_id: UUID, crm: str, draft: Draft, actor: str, now: datetime
) -> Requested:
    """An idempotent write intent."""
    h = payloads.content_hash(draft.kind, draft.account_record_id, draft.title, draft.body)
    row = (
        await s.execute(
            text(
                "INSERT INTO crm_writes (account_id, crm, kind, content_hash, record_id, title,"
                " body,"
                " marker, requested_by, created_at) VALUES (:a, :c, :k, :h, :r, :t, :b, :m, :by,"
                " :now)"
                " ON CONFLICT (tenant_id, account_id, crm, kind, content_hash) DO NOTHING"
                " RETURNING id"
            ),
            {
                "a": account_id,
                "c": crm,
                "k": draft.kind,
                "h": h,
                "r": draft.account_record_id,
                "t": draft.title,
                "b": draft.body,
                "m": draft.marker,
                "by": actor,
                "now": now,
            },
        )
    ).first()
    if row is None:
        existing = (
            await s.execute(
                text(
                    "SELECT id FROM crm_writes WHERE account_id = :a AND crm = :c AND kind = :k"
                    " AND content_hash = :h"
                ),
                {"a": account_id, "c": crm, "k": draft.kind, "h": h},
            )
        ).one()
        return Requested(UUID(str(existing.id)), False)
    await _write_event(s, row.id, "requested", actor, now)
    return Requested(UUID(str(row.id)), True)


async def _write_event(
    s: Any,
    write_id: UUID,
    event: str,
    actor: str,
    now: datetime,
    record: str | None = None,
    reason: str | None = None,
) -> bool:
    row = (
        await s.execute(
            text(
                "INSERT INTO crm_write_events (write_id, event, actor, created_record_id, reason,"
                " at)"
                " VALUES (:w, :e, :a, :r, :why, :now) ON CONFLICT DO NOTHING RETURNING id"
            ),
            {"w": write_id, "e": event, "a": actor, "r": record, "why": reason, "now": now},
        )
    ).first()
    return row is not None


async def approve(s: Any, write_ids: list[UUID], *, actor: str, now: datetime) -> int:
    """Approve a batch; returns how many were newly approved."""
    if len(write_ids) > int(config()["limits"]["items_per_batch"]):
        raise WriteRefused("batch_too_large")
    n = 0
    for w in write_ids:
        n += await _write_event(s, w, "approved", actor, now)
    return n


async def _events(s: Any, write_id: UUID) -> set[str]:
    rows = (
        await s.execute(
            text("SELECT event FROM crm_write_events WHERE write_id = :w"), {"w": write_id}
        )
    ).all()
    return {r.event for r in rows}


async def execute(
    txn: TxnFactory, tenant: UUID, client: CrmClient, write_id: UUID, *, actor: str, now: datetime
) -> str:
    """One create attempt; returns the outcome."""
    async with txn(tenant) as s:
        w = (await s.execute(text("SELECT * FROM crm_writes WHERE id = :w"), {"w": write_id})).one()
        seen = await _events(s, write_id)
        if seen & {"created", "adopted", "failed", "refused"}:
            return sorted(seen & {"created", "adopted", "failed", "refused"})[0]
        if "sending" in seen:
            return "sending"  # an attempt is in flight or crashed: reconcile, never retry
        if not await enabled(s, w.crm):
            await _write_event(s, write_id, "refused", actor, now, reason="not_enabled")
            return "refused"
        if config()["approval_per_batch"] and "approved" not in seen:
            await _write_event(s, write_id, "refused", actor, now, reason="not_approved")
            return "refused"
        await _write_event(s, write_id, "sending", actor, now)
    draft = Draft(w.kind, w.record_id, w.title, w.body, w.marker)
    try:
        created = await client.create(draft)  # no transaction open across the CRM call
    except Exception:
        # we cannot know whether the CRM created the record (a lost reply): leave the write in
        # ``sending`` for ``reconcile``, which decides by a read-only marker search
        record("crm_writes_total", 1, {"outcome": "unknown"})
        return "unknown"
    async with txn(tenant) as s:
        await _write_event(s, write_id, "created", actor, now, record=created.record_id)
    record("crm_writes_total", 1, {"outcome": "created"})
    return "created"


async def reconcile(
    txn: TxnFactory, tenant: UUID, client: CrmClient, *, actor: str, now: datetime
) -> dict[str, str]:
    """Resolve writes left in ``sending`` by a read-only marker search."""
    async with txn(tenant) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT w.id, w.marker FROM crm_writes w WHERE EXISTS (SELECT 1 FROM"
                    " crm_write_events"
                    " e WHERE e.write_id = w.id AND e.event = 'sending') AND NOT EXISTS (SELECT 1"
                    " FROM"
                    " crm_write_events e WHERE e.write_id = w.id AND e.event IN ('created',"
                    " 'adopted',"
                    " 'failed')) ORDER BY w.id LIMIT 100"
                )
            )
        ).all()
    out: dict[str, str] = {}
    for r in rows:  # LOOP(CRM reconcile): cap=100 writes per pass; exit=end
        found = await client.find_by_marker(r.marker)
        async with txn(tenant) as s:
            if found is not None:
                await _write_event(s, r.id, "adopted", actor, now, record=found.record_id)
                out[str(r.id)] = "adopted"
            else:
                await _write_event(s, r.id, "failed", actor, now, reason="not_found_after_crash")
                out[str(r.id)] = "failed"
                record("crm_writes_total", 1, {"outcome": "failed"})
    return out
