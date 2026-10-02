from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.contracts import PRODUCT_GRADES
from src.db.repositories.normalization import normalize_source_url


async def upsert_tech_detection(
    session: AsyncSession,
    *,
    account_id: UUID,
    technology: str,
    evidence_url: str,
    evidence_text: str,
    first_seen_at: datetime,
    last_seen_at: datetime,
    cross_check_result: str = "unverified",
    cross_checked_at: datetime | None = None,
    corroborating_posting_ids: list[UUID] | None = None,
    product_grade: str = "lead",
) -> UUID:
    if cross_check_result not in {"unverified", "corroborated", "contradicted"}:
        raise DataLayerError(f"invalid technology cross-check result: {cross_check_result}")
    if product_grade not in PRODUCT_GRADES:
        raise DataLayerError(f"invalid technology product grade: {product_grade}")
    if cross_check_result != "corroborated" and product_grade != "lead":
        raise DataLayerError("a date-range-only or unverified detection must remain a Lead")
    if last_seen_at < first_seen_at:
        raise DataLayerError("last_seen_at must not precede first_seen_at")
    normalized_url = normalize_source_url(evidence_url)
    evidence_hash = hashlib.sha256(
        f"{normalized_url}\0{' '.join(evidence_text.split())}".encode()
    ).hexdigest()
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to write technology detections")
    detection_id = UUID(str((await session.execute(text("SELECT gen_random_uuid()"))).scalar_one()))
    result = await session.execute(
        text(
            "INSERT INTO tech_detections (id, tenant_id, account_id, technology, evidence_url, "
            "evidence_hash, evidence_text, first_seen_at, last_seen_at, cross_check_result, "
            "cross_checked_at, corroborating_posting_ids, product_grade) "
            "VALUES (:id, :tenant_id, :account_id, :technology, :evidence_url, :evidence_hash, "
            ":evidence_text, :first_seen_at, :last_seen_at, :cross_check_result, "
            ":cross_checked_at, "
            "CAST(:posting_ids AS uuid[]), :product_grade) "
            "ON CONFLICT (tenant_id, account_id, technology, evidence_hash) DO UPDATE SET "
            "last_seen_at = greatest(tech_detections.last_seen_at, EXCLUDED.last_seen_at), "
            "cross_check_result = CASE WHEN EXCLUDED.cross_check_result = 'corroborated' "
            "THEN EXCLUDED.cross_check_result ELSE tech_detections.cross_check_result END, "
            "cross_checked_at = coalesce(EXCLUDED.cross_checked_at, "
            "tech_detections.cross_checked_at), "
            "corroborating_posting_ids = coalesce((SELECT array_agg(DISTINCT x) FROM unnest("
            "tech_detections.corroborating_posting_ids || EXCLUDED.corroborating_posting_ids) x), "
            "'{}'::uuid[]), "
            "product_grade = CASE WHEN EXCLUDED.cross_check_result = 'corroborated' "
            "THEN EXCLUDED.product_grade ELSE 'lead' END RETURNING id"
        ),
        {
            "id": detection_id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "technology": technology,
            "evidence_url": normalized_url,
            "evidence_hash": evidence_hash,
            "evidence_text": evidence_text,
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "cross_check_result": cross_check_result,
            "cross_checked_at": cross_checked_at,
            "posting_ids": corroborating_posting_ids or [],
            "product_grade": product_grade,
        },
    )
    return UUID(str(result.scalar_one()))
