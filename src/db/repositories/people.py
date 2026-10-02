from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import DataLayerError
from src.db.repositories.normalization import normalize_source_url


async def get_or_create_person(
    session: AsyncSession,
    *,
    account_id: UUID,
    name: str,
    title: str,
    profile_url: str | None = None,
    level: str | None = None,
    lane: str | None = None,
    priority: str | None = None,
    role_started_at: datetime | None = None,
    tenure_months: int | None = None,
    excluded: bool = False,
    exclusion_reason: str | None = None,
) -> UUID:
    """Deduplicate by profile URL first and normalized name second; never deletes rows."""
    normalized_name = " ".join(name.casefold().split())
    if not normalized_name or not title.strip():
        raise DataLayerError("person name and title are required")
    if priority not in {None, "A", "B", "C"}:
        raise DataLayerError("person priority must be A, B, C, or unset")
    if excluded and not (exclusion_reason and exclusion_reason.strip()):
        raise DataLayerError("an excluded person requires an exclusion reason")
    normalized_profile_url = normalize_source_url(profile_url) if profile_url else None
    profile_hash = (
        hashlib.sha256(normalized_profile_url.encode("utf-8")).hexdigest()
        if normalized_profile_url
        else None
    )
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to write people")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"{tenant_id}:person:{account_id}"},
    )

    person = None
    if profile_hash:
        person = (
            await session.execute(
                text(
                    "SELECT id FROM people WHERE tenant_id = :tenant_id "
                    "AND account_id = :account_id "
                    "AND profile_url_hash = :profile_hash FOR UPDATE"
                ),
                {
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "profile_hash": profile_hash,
                },
            )
        ).one_or_none()
    if person is None:
        profile_clause = "AND profile_url_hash IS NULL" if profile_hash is not None else ""
        person = (
            await session.execute(
                text(
                    "SELECT id FROM people WHERE tenant_id = :tenant_id "
                    "AND account_id = :account_id "
                    "AND normalized_name = :normalized_name "
                    f"{profile_clause} ORDER BY first_seen_at LIMIT 1 FOR UPDATE"
                ),
                {
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "normalized_name": normalized_name,
                },
            )
        ).one_or_none()

    values = {
        "tenant_id": tenant_id,
        "account_id": account_id,
        "name": name.strip(),
        "normalized_name": normalized_name,
        "profile_url": normalized_profile_url,
        "profile_hash": profile_hash,
        "title": title.strip(),
        "level": level,
        "lane": lane,
        "priority": priority,
        "role_started_at": role_started_at.date() if role_started_at else None,
        "tenure_months": tenure_months,
        "excluded": excluded,
        "exclusion_reason": exclusion_reason.strip() if exclusion_reason else None,
    }
    if person is not None:
        person_id = UUID(str(person.id))
        await session.execute(
            text(
                "UPDATE people SET name = :name, normalized_name = :normalized_name, "
                "profile_url = coalesce(profile_url, :profile_url), "
                "profile_url_hash = coalesce(profile_url_hash, :profile_hash), "
                "title = :title, level = coalesce(:level, level), lane = coalesce(:lane, lane), "
                "priority = coalesce(:priority, priority), "
                "role_started_at = coalesce(:role_started_at, role_started_at), "
                "tenure_months = coalesce(:tenure_months, tenure_months), "
                "excluded = excluded OR :excluded, "
                "exclusion_reason = coalesce(:exclusion_reason, exclusion_reason), "
                "last_seen_at = now() WHERE tenant_id = :tenant_id AND id = :person_id"
            ),
            {**values, "person_id": person_id},
        )
        return person_id

    person_id = UUID(str((await session.execute(text("SELECT gen_random_uuid()"))).scalar_one()))
    await session.execute(
        text(
            "INSERT INTO people (id, tenant_id, account_id, name, normalized_name, profile_url, "
            "profile_url_hash, title, level, lane, priority, role_started_at, tenure_months, "
            "excluded, exclusion_reason) VALUES (:person_id, :tenant_id, :account_id, :name, "
            ":normalized_name, :profile_url, :profile_hash, :title, :level, :lane, :priority, "
            ":role_started_at, :tenure_months, :excluded, :exclusion_reason)"
        ),
        {**values, "person_id": person_id},
    )
    return person_id

async def merge_person(
    session: AsyncSession, *, account_id: UUID, source_person_id: UUID, target_person_id: UUID
) -> None:
    if source_person_id == target_person_id:
        raise DataLayerError("a person cannot be merged into itself")
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to merge people")
    result = await session.execute(
        text(
            "UPDATE people SET merged_into = :target_id, last_seen_at = now() "
            "WHERE tenant_id = :tenant_id AND account_id = :account_id AND id = :source_id "
            "AND merged_into IS NULL AND EXISTS (SELECT 1 FROM people target "
            "WHERE target.tenant_id = :tenant_id AND target.account_id = :account_id "
            "AND target.id = :target_id AND target.merged_into IS NULL "
            "AND target.id <> :source_id)"
        ),
        {
            "target_id": target_person_id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "source_id": source_person_id,
        },
    )
    if result.rowcount != 1:
        raise DataLayerError(
            "source and target people must be visible and unmerged in this account"
        )
