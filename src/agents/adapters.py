"""Production adapters of the agent ports (Postgres under RLS, the model router).

* ``PgManifestStore``, ``PgConsentStore`` and ``PgSenseChecks`` use ``tenant_txn``: one transaction
  per call, tenant context from the trusted run context, insert-only tables (migration 0041).
* ``RouterModel`` binds ``AgentModel`` to ``llm.port.Router`` (routing by task kind, validation,
  call records): split mode is the router's tier choice (G11).
* ``run_mode_columns``, ``build_parent`` and ``pull_option`` are the SQL the run API needs inside
  its own transaction (one write path for the mode columns).
* Every port without a production binding yet raises ``NotConfigured`` (``not_configured``).

GUARANTEE(test_pg_manifests_are_isolated_and_insert_only)
GUARANTEE(test_pg_consent_is_newest_first_and_revocation_is_a_row)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.db.session import tenant_txn
from agents.manifest import PullManifest
from agents.modes import RunPlan
from agents.ports import ConsentRecord, NotConfigured, RunContext, SenseCheckRecord
from llm.models import Scope

M = TypeVar("M", bound=BaseModel)


class PgManifestStore:
    """``ManifestStore`` on ``pull_manifests``."""

    async def save(self, manifest: PullManifest) -> None:
        """Insert once per run (a replay is a no-op)."""
        async with tenant_txn(manifest.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO pull_manifests (tenant_id, run_id, account_key, config_version,"
                    " digest, manifest, created_at) VALUES (:t, :r, :a, :v, :d,"
                    " CAST(:m AS jsonb), :c) ON CONFLICT (tenant_id, run_id) DO NOTHING"
                ),
                {
                    "t": manifest.tenant_id,
                    "r": manifest.run_id,
                    "a": manifest.account_key,
                    "v": manifest.config_version,
                    "d": manifest.digest(),
                    "m": json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
                    "c": manifest.created_at,
                },
            )

    async def _one(self, tenant_id: UUID, where: str, params: Mapping[str, Any]) -> Any:
        async with tenant_txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(f"SELECT manifest FROM pull_manifests WHERE {where}"), dict(params)
                )
            ).first()
        return None if row is None else PullManifest.model_validate(row.manifest)

    async def for_run(self, tenant_id: UUID, run_id: UUID) -> PullManifest | None:
        """The manifest of one pull run of this tenant."""
        manifest: PullManifest | None = await self._one(tenant_id, "run_id = :r", {"r": run_id})
        return manifest

    async def latest(self, tenant_id: UUID, account_key: str) -> PullManifest | None:
        """The newest manifest for the account."""
        manifest: PullManifest | None = await self._one(
            tenant_id, "account_key = :a ORDER BY created_at DESC LIMIT 1", {"a": account_key}
        )
        return manifest


class PgConsentStore:
    """``ConsentStore`` on ``people_data_consents`` (D-97)."""

    async def current(self, tenant_id: UUID, scope: str) -> ConsentRecord | None:
        """The newest record (a revocation is a newer row with ``revoked_at``)."""
        async with tenant_txn(tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT granted_by, granted_at, text_version, scopes, revoked_at FROM"
                        " people_data_consents WHERE :scope = ANY(scopes) ORDER BY id DESC LIMIT 1"
                    ),
                    {"scope": scope},
                )
            ).first()
        if row is None:
            return None
        return ConsentRecord(
            tenant_id,
            row.granted_by,
            row.granted_at,
            row.text_version,
            frozenset(row.scopes),
            row.revoked_at,
        )

    async def record(
        self,
        tenant_id: UUID,
        granted_by: str,
        text_version: str,
        scopes: frozenset[str],
        at: datetime,
        *,
        revoked: bool = False,
    ) -> None:
        """Insert a grant (or, with ``revoked``, a revocation) row."""
        async with tenant_txn(tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO people_data_consents (tenant_id, granted_by, granted_at,"
                    " text_version, scopes, revoked_at) VALUES (:t, :b, :g, :v, :s, :r)"
                ),
                {
                    "t": tenant_id,
                    "b": granted_by,
                    "g": at,
                    "v": text_version,
                    "s": sorted(scopes),
                    "r": at if revoked else None,
                },
            )


class PgSenseChecks:
    """The sense-check half of ``RunRecordStore`` on ``abm_sense_checks``."""

    async def save_sense_check(self, ctx: RunContext, record: SenseCheckRecord) -> None:
        """Append the next result for the run (the latest one binds the build, G6)."""
        async with tenant_txn(ctx.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO abm_sense_checks (tenant_id, run_id, seq, manifest_digest,"
                    " facts_digest, findings, proposals) SELECT :t, :r,"
                    " COALESCE(MAX(seq), 0) + 1, :m, :f, :n, :p FROM abm_sense_checks"
                    " WHERE run_id = :r"
                ),
                {
                    "t": ctx.tenant_id,
                    "r": ctx.run_id,
                    "m": record.manifest_digest,
                    "f": record.facts_digest,
                    "n": record.findings,
                    "p": record.proposals,
                },
            )

    async def sense_check(self, ctx: RunContext) -> SenseCheckRecord | None:
        """The run's latest sense-check result."""
        async with tenant_txn(ctx.tenant_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT manifest_digest, facts_digest, findings, proposals FROM"
                        " abm_sense_checks WHERE run_id = :r ORDER BY seq DESC LIMIT 1"
                    ),
                    {"r": ctx.run_id},
                )
            ).first()
        if row is None:
            return None
        return SenseCheckRecord(
            ctx.run_id, row.manifest_digest, row.facts_digest, row.findings, row.proposals
        )

    async def save_distill(self, ctx: RunContext, audience: str, output: BaseModel) -> None:
        """Distill outputs feed the export builders' narrative input: not bound yet."""
        not_configured("distill narrative store")


@dataclass(frozen=True)
class RouterModel:
    """``AgentModel`` on the model router (``llm.port.Router``)."""

    router: Any

    async def call(
        self,
        task_kind: str,
        prompt_text: str,
        prompt_hash: str,
        payload: Mapping[str, Any],
        output: type[M],
        ctx: RunContext,
    ) -> tuple[M, int]:
        """One routed call; tokens are the call record's input plus output tokens."""
        routed = await self.router.call(
            task_kind,
            prompt_text,
            prompt_hash,
            payload,
            output,
            Scope(tenant_id=ctx.tenant_id, run_id=ctx.run_id),
        )
        usage = routed.record.usage
        result: M = routed.output
        return result, int(usage.input_tokens + usage.output_tokens)


def not_configured(what: str) -> NoReturn:
    """Fail closed for a port with no production binding yet."""
    raise NotConfigured(f"agent port {what} has no production adapter")


async def run_mode_columns(s: AsyncSession, run_id: UUID, plan: RunPlan) -> None:
    """Record mode, lineage and the config pin on the run row (inside the create transaction)."""
    await s.execute(
        text(
            "UPDATE account_runs SET run_mode = :m, parent_run_id = :p,"
            " skill_config_version = :v, skill_config_reason = :why WHERE id = :r"
        ),
        {
            "m": plan.mode.value,
            "p": plan.parent_run_id,
            "v": plan.config_version,
            "why": plan.config_version_reason,
            "r": run_id,
        },
    )


@dataclass(frozen=True)
class ParentRun:
    """What a build run needs to know about its pull parent."""

    run_id: UUID
    mode: str
    domain: str
    config_version: str | None
    has_manifest: bool


async def build_parent(s: AsyncSession, run_id: UUID) -> ParentRun | None:
    """The parent run of this tenant (RLS: another tenant's run is None)."""
    row = (
        await s.execute(
            text(
                "SELECT r.id, r.run_mode, a.domain, r.skill_config_version,"
                " EXISTS (SELECT 1 FROM pull_manifests m WHERE m.run_id = r.id) AS has_manifest"
                " FROM account_runs r JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    if row is None:
        return None
    return ParentRun(row.id, row.run_mode, row.domain, row.skill_config_version, row.has_manifest)


async def pull_option(s: AsyncSession, account_key: str) -> dict[str, Any] | None:
    """The newest usable pull for "Phase 2 only": run id, date and coverage counts."""
    row = (
        await s.execute(
            text(
                "SELECT run_id, created_at, manifest FROM pull_manifests WHERE account_key = :a"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": account_key},
        )
    ).first()
    if row is None:
        return None
    rows = row.manifest["rows"]
    stored = sum(1 for r in rows if r["status"] == "stored")
    return {
        "pull_run_id": str(row.run_id),
        "pulled_at": row.created_at.isoformat(),
        "rows_stored": stored,
        "rows_blocked": len(rows) - stored,
    }
