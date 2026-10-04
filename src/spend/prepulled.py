"""Operator-supplied pre-pulled files and the exhaustion path (D-31).

PRD 3.5: when a provider's credits run out, supply a pre-pulled file rather than fall back to a
weaker source. So:

* ``register`` records a file once per (run, provider, operation): its SHA-256, who supplied it,
  where it is stored and how many records it holds. Insert-only; a second registration of the same
  operation is ignored (the first stands).
* ``lookup`` is read by the connector before a paid call: a registered file is consumed instead of
  the call. The connector parses it with the provider's own parser (the same schema check as a live
  response), refuses it when the hash or the record count differ, and records the consumption in the
  ledger as a done call at zero cost (``cost_source='prepulled'``), so the tracker's proof and the
  rollups see it like any other call. It never touches the cap.
* ``fallback_allowed``: a fallback provider on credit exhaustion only when the tenant policy sets
  ``allow_fallback_on_exhaustion: true``; the default comes from ``config/spend.yaml`` (false).

GUARANTEE(test_prepulled_file_replaces_the_paid_call_at_zero_cost): consumed instead of a call.
GUARANTEE(test_exhaustion_does_not_fall_back_unless_the_tenant_allows_it): D-31 flag.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FLAG = "allow_fallback_on_exhaustion"


@dataclass(frozen=True)
class PrepulledFile:
    """A registered file for one (run, provider, operation)."""

    sha256: str
    object_ref: str
    record_count: int


async def register(
    s: AsyncSession,
    run_id: UUID,
    provider_code: str,
    operation: str,
    body: bytes,
    record_count: int,
    supplied_by: str,
    object_ref: str,
) -> str:
    """Record the file once (the first registration stands). Returns its SHA-256."""
    if record_count < 0:
        raise ValueError("a record count cannot be negative")
    digest = hashlib.sha256(body).hexdigest()
    await s.execute(
        text(
            "INSERT INTO prepulled_files (run_id, provider_code, operation, sha256, supplied_by,"
            " object_ref, record_count) VALUES (:r, :p, :o, :h, :by, :ref, :n)"
            " ON CONFLICT (run_id, provider_code, operation) DO NOTHING"
        ),
        {
            "r": run_id,
            "p": provider_code,
            "o": operation,
            "h": digest,
            "by": supplied_by[:200],
            "ref": object_ref,
            "n": record_count,
        },
    )
    return digest


async def lookup(
    s: AsyncSession, run_id: UUID, provider_code: str, operation: str
) -> PrepulledFile | None:
    """The registered file for this operation of the run, or None."""
    row = (
        await s.execute(
            text(
                "SELECT sha256, object_ref, record_count FROM prepulled_files WHERE run_id = :r"
                " AND provider_code = :p AND operation = :o"
            ),
            {"r": run_id, "p": provider_code, "o": operation},
        )
    ).first()
    return None if row is None else PrepulledFile(row.sha256, row.object_ref, row.record_count)


def fallback_allowed(tenant_policy: Mapping[str, Any] | None, default: bool) -> bool:
    """D-31: the tenant policy decides; the configured default (false) applies otherwise. Only a
    literal ``true`` enables it."""
    value = (tenant_policy or {}).get(FLAG, default)
    return value is True


async def tenant_fallback_allowed(s: AsyncSession, tenant_id: UUID, default: bool) -> bool:
    """``fallback_allowed`` over the tenant's stored policy (read under its own RLS context)."""
    policy = (
        await s.execute(text("SELECT policy_json FROM tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    return fallback_allowed(policy, default)
