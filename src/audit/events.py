"""Audit events: one insert-only, hash-chained record per tenant (AU1, AU2, D-69 proposal).

``record`` appends one event inside the CALLER's transaction: for runs, exports and
administration that is the action's own transaction, so the action and its audit row commit
together or not at all (an audit failure fails the action). Reads are recorded by the router
guard in a short transaction of their own, deduplicated per actor, action and resource within the
configured window (one row across every API instance: a unique dedupe key, ON CONFLICT DO
NOTHING).

The chain: ``hash = SHA-256(prev_hash || canonical(fields))`` with a gapless per-tenant ``seq``.
Writers take a transaction-scoped advisory lock keyed by the tenant before reading the chain's
head, so two concurrent writers can never both extend the same head.

Only identifiers, category codes, routes, request ids and outcomes are stored: never content,
tokens, provider names or costs (``validate`` refuses anything else).

GUARANTEE(test_audit_chain_is_gapless_and_serialised_under_concurrency)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR

GENESIS = "0" * 64
ActorKind = Literal["user", "operator", "support", "system", "idp"]
AuditClass = Literal["read", "run", "export", "admin", "access"]
Outcome = Literal["ok", "denied", "error"]
_ACTION = re.compile(r"^[a-z_]+(\.[a-z_]+){1,3}$")
_TYPE = re.compile(r"^[a-z_]{1,40}$")
_ID = re.compile(r"^[A-Za-z0-9:_.@/+=-]{1,300}$")


class AuditRefused(ValueError):
    """An event that would store something other than identifiers and categories."""


@dataclass(frozen=True)
class AuditPolicy:
    """``config/audit.yaml``."""

    read_dedupe_window_s: int
    read_fail_closed: bool
    chain_check_batch: int
    chain_check_max_batches: int
    export_max_rows: int


@lru_cache
def load_policy() -> AuditPolicy:
    """The audit policy from configuration (read once)."""
    raw = yaml.safe_load((CONFIG_DIR / "audit.yaml").read_text())
    return AuditPolicy(
        read_dedupe_window_s=int(raw["read_dedupe_window_s"]),
        read_fail_closed=bool(raw["read_fail_closed"]),
        chain_check_batch=int(raw["chain_check_batch"]),
        chain_check_max_batches=int(raw["chain_check_max_batches"]),
        export_max_rows=int(raw["export_max_rows"]),
    )


@dataclass(frozen=True)
class AuditEvent:
    """One audit fact: who did what to which resource, when, and how it ended."""

    at: datetime
    actor: str
    actor_kind: ActorKind
    audit_class: AuditClass
    action: str
    resource_type: str
    resource_id: str | None = None
    route: str | None = None
    request_id: str | None = None
    outcome: Outcome = "ok"
    support_grant_id: str | None = None


def validate(event: AuditEvent) -> None:
    """Refuse an event whose fields are not plain identifiers and category codes."""
    if not _ACTION.match(event.action) or not _TYPE.match(event.resource_type):
        raise AuditRefused("action and resource type must be category codes")
    if event.resource_id is not None and not _ID.match(event.resource_id):
        raise AuditRefused("resource id must be an identifier")
    if not 1 <= len(event.actor) <= 300 or any(c.isspace() for c in event.actor):
        raise AuditRefused("actor must be an identifier")
    if event.route is not None and (len(event.route) > 200 or not event.route.startswith("/")):
        raise AuditRefused("route must be a route template")


def canonical(event: AuditEvent, seq: int) -> bytes:
    """The bytes the chain hashes: every field, sorted, plus the sequence number."""
    fields = asdict(event)
    fields["at"] = event.at.astimezone(UTC).isoformat()
    fields["seq"] = seq
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def chain_hash(prev_hash: str, event: AuditEvent, seq: int) -> str:
    """SHA-256 over the previous hash and this event's canonical bytes."""
    return hashlib.sha256(prev_hash.encode() + b"|" + canonical(event, seq)).hexdigest()


def dedupe_key(event: AuditEvent, window_s: int) -> str:
    """The read dedupe key: actor, action, resource and the window bucket."""
    bucket = int(event.at.timestamp()) // max(1, window_s)
    raw = f"{event.actor}|{event.action}|{event.resource_type}|{event.resource_id}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def record(s: AsyncSession, event: AuditEvent, *, dedupe: str | None = None) -> int | None:
    """Append ``event`` to the context tenant's chain inside the caller's transaction; returns
    its seq, or None when ``dedupe`` names a row already recorded in this window."""
    validate(event)
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('audit:' || app_tenant()::text, 0))")
    )
    head: Any = (
        await s.execute(text("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1"))
    ).first()
    seq = 1 if head is None else int(head.seq) + 1
    prev = GENESIS if head is None else str(head.hash)
    row = (
        await s.execute(
            text(
                "INSERT INTO audit_events (seq, at, actor, actor_kind, audit_class, action,"
                " resource_type, resource_id, route, request_id, outcome, support_grant_id,"
                " dedupe_key, prev_hash, hash) VALUES (:seq, :at, :actor, :actor_kind,"
                " :audit_class, :action, :resource_type, :resource_id, :route, :request_id,"
                " :outcome, :support_grant_id, :dedupe, :prev, :hash)"
                " ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING seq"
            ),
            {
                **asdict(event),
                "seq": seq,
                "dedupe": dedupe,
                "prev": prev,
                "hash": chain_hash(prev, event, seq),
            },
        )
    ).first()
    return None if row is None else int(row.seq)
