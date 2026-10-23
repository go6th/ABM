"""Mail and drive lifecycle (owner rules 2026-10-14): what the per-user sources look like, the
"Sync now" of one user's own connections, and the purge when the user's access or the client's
feature ends.

* ``sources``: one row per source kind for the signed-in user (the newest live connection of
  that kind, its last sync outcome and error code, and the items matched to the user's accounts).
* ``owner_connections``: the user's own Mail and drive connections (a ``provider`` is set),
  optionally of some kinds and statuses. The admin's tenant-wide connector framework (no
  provider) is never touched here.
* ``sync_sources``: one sync of each listed connection through ``maildrive.sync_connection``;
  one failure never stops the others and each answer is a plain state code.
* ``sync_blocker``: why a due connection must not be synced (the client turned the feature off,
  or the owner's access ended); the scheduled sync purges such a connection instead.
* ``purge_owner`` / ``purge_tenant`` / ``purge_connections``: for each Mail and drive connection
  of the user or of the client, revoke upstream and destroy the stored token
  (``oauth.disconnect``; a failed revocation is logged, never fatal), delete every item it
  synced (``framework.delete_connection_items``) and record an audit event.

All SQL here reads; every write goes through ``connectors_client.oauth`` and
``connectors_client.framework`` (the tables' single writers). Tokens never leave the secret
store and are never logged or returned.

GUARANTEE(test_mail_and_drive_is_purged_when_access_or_the_feature_ends)
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from audit.events import AuditEvent, record
from connectors_client import framework, maildrive
from connectors_client import oauth as consent
from obs.logging import logger

log = logger(__name__)

FAMILIES: dict[str, tuple[str, ...]] = {
    "google": ("gmail", "google_drive"),
    "microsoft": ("outlook", "onedrive", "sharepoint"),
}
GROUP = {"gmail": "mail", "outlook": "mail"}  # every other kind is a drive
LIVE = ("active", "broken", "pending")
SYNC_STATES = ("ok", "partial", "failed", "broken", "skipped")
_CODE = re.compile(r"^[a-z_]{1,40}$")


def family_of(kind: str) -> str:
    """``google`` or ``microsoft``: the provider that signs the user in for this source."""
    return "google" if kind in FAMILIES["google"] else "microsoft"


def group_of(kind: str) -> str:
    """``mail`` or ``drive``."""
    return GROUP.get(kind, "drive")


def code(value: Any) -> str | None:
    """A stored reason or error as a plain code; anything else is ``unknown`` (never free text
    from a provider)."""
    if value is None or value == "":
        return None
    return str(value) if _CODE.match(str(value)) else "unknown"


@dataclass(frozen=True)
class Wiring:
    """The registered OAuth apps, the OAuth client and the reader (tests inject fakes through
    the container's ``extras``; production builds the HTTP clients)."""

    apps: dict[str, tuple[str, str]]
    oauth: Any
    reader: Any


def wiring(extras: Mapping[str, Any]) -> Wiring:
    """The Mail and drive collaborators from the container's ``extras``."""
    apps = dict(extras.get("maildrive_apps") or {})
    oauth = extras.get("maildrive_oauth") or maildrive.HttpOAuthClient(apps)
    reader = extras.get("maildrive_reader") or maildrive.HttpMailDriveReader()
    return Wiring(apps, oauth, reader)


# ---- reads ----------------------------------------------------------------------------------


async def sources(s: Any, user: str, kinds: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The user's newest live connection per kind (an active one before a broken one), with its
    last sync outcome and the items matched to the user's accounts."""
    rows = (
        await s.execute(
            text(
                "SELECT DISTINCT ON (kind) id, kind, status, broken_reason, created_at,"
                " last_synced_at, last_sync_status, last_error, last_error_at,"
                " last_sync_listed, last_sync_matched FROM connector_connections"
                " WHERE owner_user_id = :u AND provider IS NOT NULL AND kind = ANY(:k)"
                " AND status IN ('active', 'broken')"
                " ORDER BY kind, (status = 'active') DESC, created_at DESC"
            ),
            {"u": user, "k": list(kinds)},
        )
    ).all()
    counts = {
        r.kind: (int(r.accounts or 0), int(r.items or 0))
        for r in (
            await s.execute(
                text(
                    "SELECT c.kind, count(DISTINCT i.account_id) AS accounts,"
                    " count(DISTINCT i.resource_id) AS items FROM connector_items i"
                    " JOIN connector_connections c ON c.id = i.connection_id"
                    " WHERE i.owner_user_id = :u AND i.state = 'matched' AND c.kind = ANY(:k)"
                    " AND c.provider IS NOT NULL GROUP BY c.kind"
                ),
                {"u": user, "k": list(kinds)},
            )
        ).all()
    }
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        accounts, items = counts.get(r.kind, (0, 0))
        out[str(r.kind)] = {
            "id": UUID(str(r.id)),
            "kind": str(r.kind),
            "status": str(r.status),
            "created_at": r.created_at,
            "broken_reason": code(r.broken_reason),
            "last_synced_at": r.last_synced_at,
            "last_sync_status": r.last_sync_status
            if r.last_sync_status in ("ok", "partial", "failed")
            else None,
            "last_error": code(r.last_error),
            "last_error_at": r.last_error_at,
            "last_sync_listed": None if r.last_sync_listed is None else int(r.last_sync_listed),
            "last_sync_matched": None if r.last_sync_matched is None else int(r.last_sync_matched),
            "accounts": accounts,
            "items": items,
        }
    return out


async def family_counts(s: Any, user: str, kinds: Sequence[str]) -> tuple[int, int]:
    """Distinct accounts and items matched across some kinds (the Mail or the Drive total)."""
    row = (
        await s.execute(
            text(
                "SELECT count(DISTINCT i.account_id) AS accounts,"
                " count(DISTINCT i.resource_id) AS items FROM connector_items i"
                " JOIN connector_connections c ON c.id = i.connection_id"
                " WHERE i.owner_user_id = :u AND i.state = 'matched' AND c.kind = ANY(:k)"
                " AND c.provider IS NOT NULL"
            ),
            {"u": user, "k": list(kinds)},
        )
    ).one()
    return int(row.accounts or 0), int(row.items or 0)


async def owner_connections(
    s: Any,
    *,
    owner_user_id: str,
    kinds: Sequence[str] = maildrive.KINDS,
    statuses: Sequence[str] = LIVE,
) -> list[tuple[UUID, str]]:
    """The user's own Mail and drive connections (id, kind), oldest first."""
    rows = await s.execute(
        text(
            "SELECT id, kind FROM connector_connections WHERE owner_user_id = :u"
            " AND provider IS NOT NULL AND kind = ANY(:k) AND status = ANY(:st)"
            " ORDER BY created_at, id"
        ),
        {"u": owner_user_id, "k": list(kinds), "st": list(statuses)},
    )
    return [(UUID(str(r.id)), str(r.kind)) for r in rows]


async def _tenant_connections(s: Any) -> list[tuple[UUID, str]]:
    rows = await s.execute(
        text(
            "SELECT id, owner_user_id FROM connector_connections WHERE provider IS NOT NULL"
            " AND kind = ANY(:k) AND status = ANY(:st) ORDER BY created_at, id"
        ),
        {"k": list(maildrive.KINDS), "st": list(LIVE)},
    )
    return [(UUID(str(r.id)), str(r.owner_user_id)) for r in rows]


async def sync_blocker(s: Any, connection_id: UUID) -> str | None:
    """``mail_drive_off`` when the client turned the feature off, ``access_ended`` when the
    owner is no longer an active member with active access, else None (system scope)."""
    from account_intelligence.replica.settings import client_settings

    if not (await client_settings(s)).mail_drive_allowed:
        return "mail_drive_off"
    member = await s.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM connector_connections c JOIN memberships m"
            " ON m.user_id = c.owner_user_id WHERE c.id = :c AND m.status = 'active'"
            " AND m.access_state = 'active')"
        ),
        {"c": connection_id},
    )
    return None if member else "access_ended"


async def connection_owner(s: Any, connection_id: UUID) -> str | None:
    """The owner of one Mail and drive connection (None when it is not one)."""
    owner = await s.scalar(
        text(
            "SELECT owner_user_id FROM connector_connections WHERE id = :c AND provider IS NOT NULL"
        ),
        {"c": connection_id},
    )
    return None if owner is None else str(owner)


# ---- sync -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSync:
    """One connection's sync, as a page shows it (counts and a code only)."""

    connection_id: UUID
    kind: str
    state: str  # ok | partial | failed | broken | skipped
    listed: int
    matched: int
    reason: str | None


def outcome(result: Any) -> tuple[str, str | None]:
    """The state and reason of one ``SyncResult`` (its ``state`` when it has one; older results
    are read from ``skipped`` and ``broken``)."""
    state = getattr(result, "state", None)
    reason = getattr(result, "reason", None) or getattr(result, "broken", None)
    if state not in SYNC_STATES:
        if getattr(result, "skipped", False):
            state = "skipped"
        elif getattr(result, "broken", None):
            state = "broken"
        else:
            state = "ok"
    return str(state), code(reason)


async def sync_sources(
    txn: Any,
    tenant_id: UUID,
    connections: Sequence[tuple[UUID, str]],
    *,
    wired: Wiring,
    store: Any,
    now: datetime,
) -> list[SourceSync]:
    """One sync of each connection; a failure is that source's ``failed`` state only."""
    out: list[SourceSync] = []
    for connection_id, kind in connections:
        try:
            result = await maildrive.sync_connection(
                txn,
                tenant_id,
                connection_id,
                oauth=wired.oauth,
                reader=wired.reader,
                store=store,
                now=now,
                apps=wired.apps,
            )
        except Exception as error:  # one source's failure never stops the others; logged
            log.warning(
                "maildrive_sync_failed",
                extra={"connection_id": str(connection_id), "error": type(error).__name__},
            )
            out.append(SourceSync(connection_id, kind, "failed", 0, 0, "sync_failed"))
            continue
        state, reason = outcome(result)
        out.append(
            SourceSync(
                connection_id,
                kind,
                state,
                int(getattr(result, "listed", 0) or 0),
                int(getattr(result, "matched", 0) or 0),
                reason,
            )
        )
    return out


class Budget:
    """A wall-clock budget: no new work is started once it is spent."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.deadline = clock() + seconds

    def spent(self) -> bool:
        """True once the budget is used up."""
        return self.clock() >= self.deadline


# ---- purge ----------------------------------------------------------------------------------


@dataclass
class PurgeResult:
    """What one purge did, in counts."""

    connections: int = 0
    items_deleted: int = 0
    revoke_failed: int = 0


async def purge_connections(
    s: Any,
    connections: Sequence[tuple[UUID, str]],
    *,
    oauth: Any,
    store: Any,
    now: datetime,
    reason: str,
    actor: str,
    actor_kind: Any = "system",
) -> PurgeResult:
    """Disconnect each (connection, owner): revoke upstream and destroy the token, delete what
    it synced, mark it disconnected, and audit it (``admin.maildrive_purged``)."""
    done = PurgeResult()
    for connection_id, owner in connections:
        try:
            upstream = await consent.disconnect(
                s, connection_id=connection_id, user=owner, oauth=oauth, store=store, now=now
            )
        except consent.ConnectorRefused:
            continue  # already gone
        if upstream != "ok":
            done.revoke_failed += 1
            log.warning(
                "maildrive_revoke_failed",
                extra={"connection_id": str(connection_id), "reason": reason},
            )
        done.items_deleted += await framework.delete_connection_items(s, connection_id)
        done.connections += 1
        await record(
            s,
            AuditEvent(
                at=now,
                actor=actor,
                actor_kind=actor_kind,
                audit_class="admin",
                action="admin.maildrive_purged",
                resource_type="connector",
                resource_id=str(connection_id),
                outcome="ok" if upstream == "ok" else "error",
            ),
        )
    if done.connections:
        log.info(
            "maildrive_purged",
            extra={
                "reason": reason,
                "connections": done.connections,
                "items_deleted": done.items_deleted,
                "revoke_failed": done.revoke_failed,
            },
        )
    return done


async def purge_owner(
    s: Any,
    *,
    owner_user_id: str,
    oauth: Any,
    store: Any,
    now: datetime,
    reason: str,
    actor: str = "system:maildrive",
    actor_kind: Any = "system",
) -> PurgeResult:
    """Purge every Mail and drive connection of one user (their access ended)."""
    conns = await owner_connections(s, owner_user_id=owner_user_id)
    return await purge_connections(
        s,
        [(cid, owner_user_id) for cid, _ in conns],
        oauth=oauth,
        store=store,
        now=now,
        reason=reason,
        actor=actor,
        actor_kind=actor_kind,
    )


async def purge_tenant(
    s: Any,
    *,
    oauth: Any,
    store: Any,
    now: datetime,
    reason: str,
    actor: str = "system:maildrive",
    actor_kind: Any = "system",
) -> PurgeResult:
    """Purge every Mail and drive connection of the context client (the feature was turned
    off). Needs a scope that sees every user's connections (operator or system)."""
    return await purge_connections(
        s,
        await _tenant_connections(s),
        oauth=oauth,
        store=store,
        now=now,
        reason=reason,
        actor=actor,
        actor_kind=actor_kind,
    )


def family_kinds(family: str) -> tuple[str, ...]:
    """The kinds of one family (``google`` or ``microsoft``)."""
    return FAMILIES[family]
