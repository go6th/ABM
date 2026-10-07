"""Registries of the tenant objects that are not tables: storage layouts, cache key purposes,
queue and outbox event types, and background jobs (TN1).

The isolation matrix generator (``scripts/gen_isolation_matrix.py``) discovers tables and views
from the database catalog and routes from the application; everything else is discovered here.
Adding a storage layout, cache purpose, event type or job without registering it here, and
without a matching entry in ``tests/isolation/matrix/matrix.yaml``, breaks the build.

Pure data; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid5

from tenancy.storage import tenant_path

_NS = UUID("6f1a3c7e-0000-4000-8000-000000000001")


def _id(tenant: UUID, name: str) -> UUID:
    """A stable synthetic identifier under ``tenant`` for sample keys (no randomness)."""
    return uuid5(_NS, f"{tenant}:{name}")


@dataclass(frozen=True)
class StorageLayout:
    """An object-key layout: who builds it and a sample key for ``tenant``."""

    builder: str
    sample: Callable[[UUID], str]


def _payload(t: UUID) -> str:
    from account_intelligence.connectors.payloads import payload_key

    return payload_key(t, _id(t, "account"), _id(t, "run"), b"canary")


def _deliverable(t: UUID) -> str:
    from exports.store import object_key

    return object_key(str(t), str(_id(t, "account")), str(_id(t, "run")), "summary", "a" * 64)


STORAGE_LAYOUTS: dict[str, StorageLayout] = {
    "raw_payload": StorageLayout("account_intelligence.connectors.payloads.payload_key", _payload),
    "deliverable": StorageLayout("exports.store.object_key", _deliverable),
    "branding": StorageLayout(
        "tenancy.storage.tenant_path(branding)",
        lambda t: tenant_path(t, "branding", "a" * 64 + ".png"),
    ),
    "branding_upload_quarantine": StorageLayout(
        "tenancy.storage.tenant_path(uploads, branding)",
        lambda t: tenant_path(t, "uploads", "branding", "a" * 64),
    ),
    "connector_quarantine": StorageLayout(
        "tenancy.storage.tenant_path(connector-quarantine)",
        lambda t: tenant_path(t, "connector-quarantine", str(_id(t, "conn")), "a" * 64),
    ),
    "export_file": StorageLayout(
        "tenancy.storage.tenant_path(exports)",
        lambda t: tenant_path(t, "exports", str(_id(t, "run")), "a" * 64 + ".csv"),
    ),
    "prepulled_quarantine": StorageLayout(
        "spend.exhaustion (quarantine/<tenant>/<run>/<intent>)",
        lambda t: f"quarantine/{t}/{_id(t, 'run')}/{_id(t, 'intent')}",
    ),
}

# Cache and rate-limit key purposes. Keys are built by ``tenancy.keys.cache_key`` and always start
# with ``t:<tenant>:<purpose>:``; Redis holds nothing authoritative or private (ARCH 9).
CACHE_PURPOSES: dict[str, str] = {
    "membership": "membership role decision, about 60 s (ID2)",
}

# Event types that leave a transaction: Cloud Tasks payloads and outbox rows. Each carries the
# tenant id and identifiers only, never content (skill rule 8).
EVENT_TYPES: dict[str, str] = {
    "task.run_slice": "Cloud Tasks payload {tenant_id, run_id, slice}",
    "task.approval_timeout": "Cloud Tasks payload {tenant_id, run_id}",
    "outbox.notification": "notification_outbox row (tenant-scoped, RLS)",
    "audit.event": "audit_events row (tenant-scoped, RLS, insert-only, hash-chained)",
    "connector.access": "connector_access_log row (tenant-scoped, RLS, insert-only)",
    "usage.event": "usage_events row (tenant-scoped, RLS, insert-only, idempotent)",
}

# Background jobs and the boundary each runs under.
JOBS: dict[str, str] = {
    "spend_invariants": "spend.jobs.spend_invariants: per tenant and run, inside tenant_txn",
    "stuck_scan": "obs.stuck.scan: per tenant, inside tenant_txn",
    "cost_rollup": "obs.costs.refresh: per tenant and run, inside tenant_txn",
    "isolation_probe": "obs.canary.probe: synthetic tenants only, inside tenant_txn",
    "audit_chain_verify": "audit.chain.verify: per tenant, inside tenant_txn",
    "usage_reconcile": "metering.reconcile.reconcile: per tenant, inside tenant_txn",
    "connector_fetch": "connectors_client.framework.fetch_pass: per connection, tenant_txn;"
    " provider calls outside any transaction",
}
