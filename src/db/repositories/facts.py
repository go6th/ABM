from __future__ import annotations

import json
from datetime import UTC
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.contracts import ENGINE_GRADES, PRODUCT_GRADES, FactEvidence
from src.db.repositories.normalization import fingerprint_value


async def create_fact_with_evidence(
    session: AsyncSession,
    *,
    account_id: UUID,
    attribute_key: str,
    value: object,
    engine_grade: str,
    product_grade: str,
    confidence: float,
    visibility: str,
    read_in_primary_this_run: bool,
    evidence: list[FactEvidence],
) -> UUID:
    if not evidence:
        raise DataLayerError("a fact requires at least one evidence row")
    if not attribute_key.strip():
        raise DataLayerError("a fact requires an attribute key")
    if engine_grade not in ENGINE_GRADES or product_grade not in PRODUCT_GRADES:
        raise DataLayerError("fact grades must use configured engine and product vocabularies")
    if product_grade == "confirmed" and not read_in_primary_this_run:
        raise DataLayerError("a Confirmed fact must have been read in a primary source this run")
    if not 0 <= confidence <= 1 or visibility not in {"public", "private"}:
        raise DataLayerError("invalid confidence or visibility")
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to write a fact")
    fingerprint = fingerprint_value(attribute_key, value)
    try:
        normalized_json = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise DataLayerError(f"fact value is not valid JSON: {exc}") from exc
    fact_id = UUID(str((await session.execute(text("SELECT gen_random_uuid()"))).scalar_one()))
    async with session.begin_nested():
        await session.execute(
            text(
                "INSERT INTO facts (id, tenant_id, account_id, attribute_key, value_json, "
                "fingerprint, engine_grade, product_grade, confidence, read_in_primary_this_run, "
                "visibility) VALUES (:id, :tenant_id, :account_id, :attribute_key, "
                "CAST(:value_json AS jsonb), :fingerprint, :engine_grade, :product_grade, "
                ":confidence, :read_in_primary_this_run, :visibility)"
            ),
            {
                "id": fact_id,
                "tenant_id": tenant_id,
                "account_id": account_id,
                "attribute_key": attribute_key,
                "value_json": normalized_json,
                "fingerprint": fingerprint,
                "engine_grade": engine_grade,
                "product_grade": product_grade,
                "confidence": confidence,
                "read_in_primary_this_run": read_in_primary_this_run,
                "visibility": visibility,
            },
        )
        for item in evidence:
            if not item.quote_text.strip():
                raise DataLayerError("fact evidence requires a non-empty source quote")
            if item.retrieved_at.tzinfo is None or item.retrieved_at.utcoffset() is None:
                raise DataLayerError("fact evidence retrieval time must include a timezone")
            source = (
                await session.execute(
                    text(
                        "SELECT source_code, lineage_group FROM sources "
                        "WHERE tenant_id = :tenant_id AND account_id = :account_id AND id = :id"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "account_id": account_id,
                        "id": item.source_id,
                    },
                )
            ).one_or_none()
            if (
                source is None
                or item.source_code != source.source_code
                or item.lineage_group != source.lineage_group
            ):
                raise DataLayerError(
                    "fact evidence source code and lineage must match the referenced source"
                )
            await session.execute(
                text(
                    "INSERT INTO fact_evidence (tenant_id, account_id, fact_id, source_id, "
                    "source_code, lineage_group, quote_text, retrieved_at) "
                    "VALUES (:tenant_id, :account_id, :fact_id, :source_id, :source_code, "
                    ":lineage_group, :quote_text, :retrieved_at)"
                ),
                {
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "fact_id": fact_id,
                    "source_id": item.source_id,
                    "source_code": item.source_code,
                    "lineage_group": item.lineage_group,
                    "quote_text": item.quote_text,
                    "retrieved_at": item.retrieved_at.astimezone(UTC),
                },
            )
    return fact_id
