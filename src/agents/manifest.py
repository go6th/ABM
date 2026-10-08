"""The pull manifest (G15) and the build precondition (G16).

A pull states, per coverage row, what was pulled: payload references, the queries, tool category,
result counts, cost and time, or a Blocked reason. It never states what the data means (G5).

``check_build_precondition`` is the single decision a ``build`` run (or the build slice of a
``full`` run) makes before anything else:

1. no manifest, or one for another tenant or account: ``NoPulledData`` listing every needed row;
2. a needed row absent from the manifest: ``NoPulledData`` listing the missing rows (a Blocked row
   with a reason counts as present: it is visible, not silently absent);
3. a stored row older than its shelf life and not acknowledged: ``StalePullsNeedDecision``;
4. otherwise the precondition, with the stale rows that were acknowledged.

It never pulls: a build never silently runs Phase 1.

GUARANTEE(test_build_without_pull_fails_with_no_pulled_data)
GUARANTEE(test_stale_pull_requires_acknowledgement)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.errors import NoPulledData, StalePullsNeedDecision


class QueryRecord(BaseModel):
    """One query as the code issued it (proof for G1 and G3), never model text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=400)
    tool_category: str = Field(pattern=r"^[a-z_]+$")
    result_count: int = Field(ge=0)


class ManifestRow(BaseModel):
    """What Phase 1 holds for one coverage row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row_key: str = Field(pattern=r"^[a-z0-9_]+$")
    status: Literal["stored", "blocked"]
    payload_refs: tuple[str, ...] = ()
    call_refs: tuple[str, ...] = ()
    queries: tuple[QueryRecord, ...] = ()
    cost_micros: int = Field(default=0, ge=0)
    pulled_at: datetime
    blocked_reason: str | None = Field(default=None, pattern=r"^[a-z_]+$")

    @model_validator(mode="after")
    def _consistent(self) -> ManifestRow:
        if self.pulled_at.tzinfo is None:
            raise ValueError("pulled_at must be timezone aware")
        if self.status == "stored" and (not self.call_refs or self.blocked_reason):
            raise ValueError("a stored row needs call refs (its proof) and no blocked reason")
        if self.status == "blocked" and (self.payload_refs or not self.blocked_reason):
            raise ValueError("a blocked row needs a reason and no payload refs")
        return self


class PullManifest(BaseModel):
    """The manifest of one pull (or the pull slice of a full run)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    run_id: UUID
    account_key: str = Field(min_length=1)
    config_version: str
    created_at: datetime
    rows: tuple[ManifestRow, ...]

    def digest(self) -> str:
        """sha256 of the canonical manifest (the sense check and build bind to it, G6)."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def total_cost_micros(self) -> int:
        """Spend recorded across rows (the ledger is the authority; this is the summary)."""
        return sum(r.cost_micros for r in self.rows)


@dataclass(frozen=True)
class BuildPrecondition:
    """A usable manifest and what was acknowledged."""

    manifest: PullManifest
    acknowledged_stale: tuple[str, ...]
    blocked_rows: tuple[str, ...]


def stale_rows(
    manifest: PullManifest, shelf_life_days: Mapping[str, int | None], now: datetime
) -> tuple[str, ...]:
    """Stored rows older than their shelf life (rows with no shelf life never go stale)."""
    out = []
    for row in manifest.rows:
        days = shelf_life_days.get(row.row_key)
        if row.status == "stored" and days is not None and now - row.pulled_at > timedelta(days):
            out.append(row.row_key)
    return tuple(sorted(out))


def check_build_precondition(
    manifest: PullManifest | None,
    *,
    tenant_id: UUID,
    account_key: str,
    required_rows: Iterable[str],
    shelf_life_days: Mapping[str, int | None],
    now: datetime,
    acknowledged_stale: frozenset[str] = frozenset(),
) -> BuildPrecondition:
    """See the module docstring."""
    required = tuple(sorted(set(required_rows)))
    if manifest is None or manifest.tenant_id != tenant_id or manifest.account_key != account_key:
        raise NoPulledData(required, "no pull manifest for this tenant and account")
    present = {r.row_key for r in manifest.rows}
    missing = tuple(k for k in required if k not in present)
    if missing:
        raise NoPulledData(missing)
    stale = stale_rows(manifest, shelf_life_days, now)
    unacknowledged = tuple(k for k in stale if k not in acknowledged_stale)
    if unacknowledged:
        raise StalePullsNeedDecision(unacknowledged)
    blocked = tuple(sorted(r.row_key for r in manifest.rows if r.status == "blocked"))
    return BuildPrecondition(manifest, stale, blocked)
