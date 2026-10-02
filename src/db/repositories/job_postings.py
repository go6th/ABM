from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.normalization import normalize_source_url


async def upsert_job_posting(
    session: AsyncSession,
    *,
    account_id: UUID,
    posting_url: str,
    title: str,
    posting_class: str,
    platform_sentence: str | None,
    full_text: str | None,
    full_text_read: bool,
) -> UUID:
    if posting_class not in {"build", "admin_owner", "working_tool", "user"}:
        raise DataLayerError(f"invalid job posting class: {posting_class}")
    if full_text_read and not (full_text and full_text.strip()):
        raise DataLayerError("full_text_read requires the full posting text")
    normalized_url = normalize_source_url(posting_url)
    url_hash = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to write job postings")
    posting_id = UUID(str((await session.execute(text("SELECT gen_random_uuid()"))).scalar_one()))
    result = await session.execute(
        text(
            "INSERT INTO job_postings (id, tenant_id, account_id, posting_url, posting_url_hash, "
            "title, posting_class, platform_sentence, full_text, full_text_read) "
            "VALUES (:id, :tenant_id, :account_id, :posting_url, :url_hash, :title, "
            ":posting_class, "
            ":platform_sentence, :full_text, :full_text_read) "
            "ON CONFLICT (tenant_id, account_id, posting_url_hash) DO UPDATE SET "
            "last_seen_at = now(), title = EXCLUDED.title, posting_class = EXCLUDED.posting_class, "
            "platform_sentence = coalesce(EXCLUDED.platform_sentence, "
            "job_postings.platform_sentence), "
            "full_text = coalesce(EXCLUDED.full_text, job_postings.full_text), "
            "full_text_read = job_postings.full_text_read OR EXCLUDED.full_text_read "
            "RETURNING id"
        ),
        {
            "id": posting_id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "posting_url": normalized_url,
            "url_hash": url_hash,
            "title": title,
            "posting_class": posting_class,
            "platform_sentence": platform_sentence,
            "full_text": full_text,
            "full_text_read": full_text_read,
        },
    )
    return UUID(str(result.scalar_one()))
