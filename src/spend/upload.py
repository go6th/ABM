"""The upload step for a pre-pulled file (M8, M9): an upload is hostile input.

``create_intent`` (operator only) records the exact file spec the exhaustion ladder generated and
returns a short-lived signed target in the **quarantine** location (separate from the accepted
location). ``complete`` then makes ONE validation pass, recording every step as an insert-only
intent event, and returns a typed verdict:

1. the intent belongs to this tenant (RLS), is unexpired and undecided;
2. the object exists, its size is within the cap and its content type is allowed;
3. the malware scan passes; **an unavailable scanner fails closed** (the file stays in
   quarantine, the verdict is ``rejected: scan_unavailable``);
4. its sha256 has not been accepted for this run before (dedupe);
5. it parses in strict mode with **the same normaliser as the live provider** (``ParsePort``,
   production: the connector's own parser) into at least one record; schema drift is rejected;
6. the account matches where the records carry a domain, otherwise the operator's explicit
   attestation is required and recorded; the attested pull date is checked against the configured
   maximum age (none configured: the attestation is still required and recorded);
7. the file matches the spec's pending operation and none is registered yet for it: it is copied
   to the accepted location and registered (``spend.prepulled.register``), so the connector
   consumes it instead of a paid call at zero cost (ledger ``cost_source = prepulled``) and the
   pull-once replay serves it afterwards. Anything unmatched stays pending.

No step is retried; a rejected file needs a new intent. File content is never logged, never put
in an event, an outbox message or a client output; only its hash, size and record count are kept.

GUARANTEE(test_hostile_uploads_are_rejected_with_typed_reasons): M8 lying scenarios.
GUARANTEE(test_an_unavailable_scanner_fails_closed): M8.
GUARANTEE(test_an_accepted_upload_is_consumed_instead_of_a_paid_call): M9 end to end.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend import notify, prepulled
from spend.config import V2Config
from spend.errors import NotConfigured, SpendError

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
Reason = Literal[
    "intent_not_found",
    "intent_expired",
    "already_decided",
    "not_uploaded",
    "too_large",
    "wrong_type",
    "scan_failed",
    "scan_unavailable",
    "duplicate",
    "schema_drift",
    "empty",
    "account_mismatch",
    "attestation_missing",
    "stale",
    "already_registered",
]


class UploadError(SpendError):
    """The upload step refused a request (not a file verdict: those are ``Verdict``)."""


class ScannerUnavailable(SpendError):
    """The malware scanner could not give a verdict (fail closed)."""


@dataclass(frozen=True)
class SignedTarget:
    """Where the operator puts the file (short-lived; carries no read power)."""

    url: str
    expires_at: datetime
    max_bytes: int
    content_type: str


@dataclass(frozen=True)
class ObjectInfo:
    """What the store says about an uploaded object."""

    size: int
    content_type: str


class UploadStore(Protocol):
    """Quarantine and accepted locations (production: ``NotConfigured`` until the adapter)."""

    async def signed_put(
        self, key: str, max_bytes: int, content_type: str, expires_at: datetime
    ) -> SignedTarget:
        """A signed, size-limited upload target in quarantine."""
        ...

    async def stat(self, key: str) -> ObjectInfo | None:
        """Size and type of the quarantined object, or None when absent."""
        ...

    async def get(self, key: str) -> bytes:
        """The quarantined bytes (read only for validation)."""
        ...

    async def promote(self, key: str, accepted_key: str) -> str:
        """Copy a validated object to the accepted location; returns its reference."""
        ...


class ScanPort(Protocol):
    """Malware scan of a quarantined object."""

    async def scan(self, key: str) -> bool:
        """True when clean; False when infected; ``ScannerUnavailable`` otherwise."""
        ...


class ParsePort(Protocol):
    """The live provider's own strict normaliser (records as plain mappings)."""

    def parse(
        self, provider_code: str, operation: str, args: Mapping[str, Any], body: bytes
    ) -> list[dict[str, Any]]:
        """Parse like a live response; raise on schema drift."""
        ...


class UnconfiguredUploadStore:
    """Production placeholder (no signed upload adapter yet: never a fake success)."""

    async def signed_put(
        self, key: str, max_bytes: int, content_type: str, expires_at: datetime
    ) -> SignedTarget:
        """Always ``NotConfigured``."""
        raise NotConfigured("signed upload target")

    async def stat(self, key: str) -> ObjectInfo | None:
        """Always ``NotConfigured``."""
        raise NotConfigured("upload store")

    async def get(self, key: str) -> bytes:
        """Always ``NotConfigured``."""
        raise NotConfigured("upload store")

    async def promote(self, key: str, accepted_key: str) -> str:
        """Always ``NotConfigured``."""
        raise NotConfigured("upload store")


class UnconfiguredScanner:
    """Production placeholder: an absent scanner is an unavailable scanner (fail closed)."""

    async def scan(self, key: str) -> bool:
        """Always ``ScannerUnavailable``."""
        raise ScannerUnavailable("no malware scanner is configured")


@dataclass(frozen=True)
class Attestation:
    """What the operator states about the file (recorded with the verdict)."""

    account_confirmed: bool
    pull_date: date | None


@dataclass(frozen=True)
class Verdict:
    """The typed outcome of ``complete``."""

    accepted: bool
    reason: Reason | None
    intent_id: str
    operations: tuple[str, ...] = ()  # "provider.operation" satisfied
    record_count: int = 0
    sha256: str | None = None


async def _event(
    txn: TxnFactory,
    tenant_id: UUID,
    intent_id: UUID,
    event: str,
    now: datetime,
    *,
    reason: str | None = None,
    sha: str | None = None,
    principal: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    async with txn(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO prepulled_intent_events (intent_id, event, reason_code, sha256,"
                " principal, detail_json, at)"
                " VALUES (:i, :e, :r, :h, :p, CAST(:d AS jsonb), :at)"
            ),
            {
                "i": intent_id,
                "e": event,
                "r": reason,
                "h": sha,
                "p": principal,
                "d": json.dumps(dict(detail or {}), sort_keys=True),
                "at": now,
            },
        )


async def create_intent_target(
    txn: TxnFactory,
    tenant_id: UUID,
    intent_id: UUID,
    store: UploadStore,
    now: datetime,
    cfg: V2Config,
) -> tuple[dict[str, Any], SignedTarget]:
    """The exact spec and a signed quarantine target for an existing intent (operator route)."""
    async with txn(tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT spec_json, quarantine_key, expires_at FROM prepulled_intents"
                    " WHERE id = :i"
                ),
                {"i": intent_id},
            )
        ).first()
    if row is None:
        raise UploadError("intent_not_found")
    if row.expires_at <= now:
        raise UploadError("intent_expired")
    content_type = sorted(cfg.upload_content_types)[0]
    target = await store.signed_put(
        str(row.quarantine_key), cfg.upload_max_bytes, content_type, row.expires_at
    )
    return dict(row.spec_json), target


def _domains(records: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for record in records:
        for key in ("domain", "company_domain", "account_domain"):
            value = record.get(key)
            if isinstance(value, str) and value:
                found.add(value.lower().removeprefix("www."))
    return found


async def complete(
    txn: TxnFactory,
    tenant_id: UUID,
    intent_id: UUID,
    operator: str,
    attestation: Attestation,
    store: UploadStore,
    scanner: ScanPort,
    parser: ParsePort,
    now: datetime,
    cfg: V2Config,
) -> Verdict:
    """The one validation pass (see the module docstring)."""
    async with txn(tenant_id) as s:
        row = (
            await s.execute(
                text(
                    "SELECT i.run_id, i.provider_code, i.operation, i.spec_json, i.quarantine_key,"
                    " i.expires_at, a.domain, r.cap_version,"
                    " EXISTS (SELECT 1 FROM prepulled_intent_events e WHERE e.intent_id = i.id"
                    "   AND e.event IN ('accepted', 'rejected')) AS decided"
                    " FROM prepulled_intents i JOIN account_runs r ON r.id = i.run_id"
                    " JOIN accounts a ON a.id = r.account_id WHERE i.id = :i"
                ),
                {"i": intent_id},
            )
        ).first()
    if row is None:
        return Verdict(False, "intent_not_found", str(intent_id))
    if row.decided:
        return Verdict(False, "already_decided", str(intent_id))

    async def reject(reason: Reason, sha: str | None = None) -> Verdict:
        """Record the rejection (event, run event, operator notification) and return it."""
        await _event(
            txn, tenant_id, intent_id, "rejected", now, reason=reason, sha=sha, principal=operator
        )
        await _record(txn, tenant_id, row, intent_id, "upload_rejected", reason, now)
        return Verdict(False, reason, str(intent_id), sha256=sha)

    await _event(txn, tenant_id, intent_id, "completed", now, principal=operator)
    if row.expires_at <= now:
        return await reject("intent_expired")
    info = await store.stat(str(row.quarantine_key))
    if info is None:
        return await reject("not_uploaded")
    if info.size > cfg.upload_max_bytes:
        return await reject("too_large")
    if info.content_type not in cfg.upload_content_types:
        return await reject("wrong_type")
    try:
        clean = await scanner.scan(str(row.quarantine_key))
    except ScannerUnavailable:
        await _event(txn, tenant_id, intent_id, "scan_unavailable", now)
        return await reject("scan_unavailable")
    if not clean:
        await _event(txn, tenant_id, intent_id, "scan_failed", now)
        return await reject("scan_failed")
    await _event(txn, tenant_id, intent_id, "scan_clean", now)
    body = await store.get(str(row.quarantine_key))
    if len(body) > cfg.upload_max_bytes:  # the store's size claim is not trusted either
        return await reject("too_large")
    sha = hashlib.sha256(body).hexdigest()
    async with txn(tenant_id) as s:
        seen = (
            await s.execute(
                text(
                    "SELECT 1 FROM prepulled_files WHERE run_id = :r AND sha256 = :h"
                    " UNION ALL SELECT 1 FROM prepulled_intent_events e"
                    " JOIN prepulled_intents i ON i.id = e.intent_id"
                    " WHERE i.run_id = :r AND e.event = 'accepted' AND e.sha256 = :h"
                ),
                {"r": row.run_id, "h": sha},
            )
        ).first()
    if seen is not None:
        return await reject("duplicate", sha)
    spec = dict(row.spec_json)
    pending = spec.get("pending_calls") or []
    args = dict(pending[0]["args"]) if pending else {}
    try:
        records = parser.parse(str(row.provider_code), str(row.operation), args, body)
    except Exception:  # any parse failure is drift; the content is never echoed
        return await reject("schema_drift", sha)
    if not records:
        return await reject("empty", sha)
    domains = _domains(records)
    account = str(row.domain).lower().removeprefix("www.")
    if domains and domains != {account}:
        return await reject("account_mismatch", sha)
    if not domains and not attestation.account_confirmed:
        return await reject("attestation_missing", sha)
    if attestation.pull_date is None:
        return await reject("attestation_missing", sha)
    if cfg.upload_max_age_days is not None and now.date() - attestation.pull_date > timedelta(
        days=cfg.upload_max_age_days
    ):
        return await reject("stale", sha)
    async with txn(tenant_id) as s:
        registered = await prepulled.lookup(
            s, row.run_id, str(row.provider_code), str(row.operation)
        )
    if registered is not None:
        return await reject("already_registered", sha)
    accepted_key = f"prepulled/{tenant_id}/{row.run_id}/{sha}.json"
    ref = await store.promote(str(row.quarantine_key), accepted_key)
    async with txn(tenant_id) as s:
        await prepulled.register(
            s,
            row.run_id,
            str(row.provider_code),
            str(row.operation),
            body,
            len(records),
            operator,
            ref,
        )
    await _event(
        txn,
        tenant_id,
        intent_id,
        "accepted",
        now,
        sha=sha,
        principal=operator,
        detail={
            "record_count": len(records),
            "account_attested": attestation.account_confirmed,
            "pull_date": attestation.pull_date.isoformat(),
            "bytes": len(body),
        },
    )
    await _record(txn, tenant_id, row, intent_id, "upload_accepted", "accepted", now)
    return Verdict(
        True, None, str(intent_id), (f"{row.provider_code}.{row.operation}",), len(records), sha
    )


async def _record(
    txn: TxnFactory,
    tenant_id: UUID,
    row: Any,
    intent_id: UUID,
    event: str,
    reason: str,
    now: datetime,
) -> None:
    """The run-level event and the operator notification of a verdict (M12)."""
    async with txn(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO spend_limit_events (run_id, cap_version, event, detail_json, at)"
                " VALUES (:r, :v, :e, CAST(:d AS jsonb), :at)"
            ),
            {
                "r": row.run_id,
                "v": row.cap_version or 1,
                "e": event,
                "d": json.dumps({"intent_id": str(intent_id), "reason_code": reason}),
                "at": now,
            },
        )
        await notify.enqueue(
            s,
            row.run_id,
            f"upload:{intent_id}",
            "operator",
            "upload_result",
            {
                "run_id": str(row.run_id),
                "intent_id": str(intent_id),
                "outcome": "accepted" if event == "upload_accepted" else "rejected",
                "reason_code": reason,
            },
            now,
        )
