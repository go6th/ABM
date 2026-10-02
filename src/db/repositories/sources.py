from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.contracts import SOURCE_CODE_FORMAT
from src.db.repositories.normalization import normalize_source_url, source_url_hash


async def get_or_create_source(
    session: AsyncSession,
    account_id: UUID,
    url: str,
    *,
    tier: str = "T4",
    lineage_group: str | None = None,
    content_hash: str | None = None,
) -> tuple[str, UUID]:
    """Allocate a stable per-account source code while holding its counter row lock.

    The caller must use ``tenant_txn`` so tenant identity comes from transaction context.
    """
    if tier not in {"T1", "T2", "T3", "T4"}:
        raise DataLayerError(f"invalid source tier: {tier}")
    normalized_url = normalize_source_url(url)
    url_hash = source_url_hash(normalized_url)
    domain = urlsplit(normalized_url).hostname
    if domain is None:
        raise DataLayerError("normalized source URL has no domain")
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to create a source")

    found = (
        await session.execute(
            text(
                "SELECT source_code, id FROM sources "
                "WHERE tenant_id = :tenant_id AND account_id = :account_id AND url_hash = :url_hash"
            ),
            {"tenant_id": tenant_id, "account_id": account_id, "url_hash": url_hash},
        )
    ).one_or_none()
    if found is not None:
        await session.execute(
            text(
                "UPDATE sources SET last_seen_at = now(), "
                "content_hash = coalesce(:content_hash, content_hash) "
                "WHERE tenant_id = :tenant_id AND account_id = :account_id AND id = :source_id"
            ),
            {
                "content_hash": content_hash,
                "tenant_id": tenant_id,
                "account_id": account_id,
                "source_id": found.id,
            },
        )
        return found.source_code, found.id

    await session.execute(
        text(
            "INSERT INTO source_code_counters (tenant_id, account_id, next_value) "
            "SELECT :tenant_id, :account_id, "
            "coalesce(max(substring(source_code from 3)::bigint), 0) + 1 "
            "FROM sources WHERE tenant_id = :tenant_id AND account_id = :account_id "
            "ON CONFLICT DO NOTHING"
        ),
        {"tenant_id": tenant_id, "account_id": account_id},
    )
    counter = (
        await session.execute(
            text(
                "SELECT next_value FROM source_code_counters "
                "WHERE tenant_id = :tenant_id AND account_id = :account_id FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "account_id": account_id},
        )
    ).one()

    found = (
        await session.execute(
            text(
                "SELECT source_code, id FROM sources "
                "WHERE tenant_id = :tenant_id AND account_id = :account_id AND url_hash = :url_hash"
            ),
            {"tenant_id": tenant_id, "account_id": account_id, "url_hash": url_hash},
        )
    ).one_or_none()
    if found is not None:
        return found.source_code, found.id

    code = SOURCE_CODE_FORMAT.format(value=counter.next_value)
    source_id = UUID(str((await session.execute(text("SELECT gen_random_uuid()"))).scalar_one()))
    await session.execute(
        text(
            "UPDATE source_code_counters SET next_value = next_value + 1 "
            "WHERE tenant_id = :tenant_id AND account_id = :account_id"
        ),
        {"tenant_id": tenant_id, "account_id": account_id},
    )
    await session.execute(
        text(
            "INSERT INTO sources (id, tenant_id, account_id, source_code, url, url_hash, "
            "domain, tier, lineage_group, content_hash) "
            "VALUES (:id, :tenant_id, :account_id, :source_code, :url, :url_hash, :domain, "
            ":tier, :lineage_group, :content_hash)"
        ),
        {
            "id": source_id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "source_code": code,
            "url": normalized_url,
            "url_hash": url_hash,
            "domain": domain,
            "tier": tier,
            "lineage_group": lineage_group or f"source:{url_hash}",
            "content_hash": content_hash,
        },
    )
    return code, source_id
