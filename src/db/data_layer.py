"""Tenant-scoped repositories and validated checklist operations for data layer v1."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CHECKLIST = Path(__file__).resolve().parents[2] / "config" / "checklist.yaml"
CoverageStatus = Literal["Done", "Partial", "Not done", "Blocked", "None"]
SOURCE_CODE_FORMAT = "S-{value:04d}"
PRODUCT_GRADES = {"confirmed", "probable", "hypothesis", "lead"}
ENGINE_GRADES = {"A", "B", "C", "D", "CONFLICTING", "BLOCKED"}


class DataLayerError(ValueError):
    """Invalid data-layer input or an attempted invalid state transition."""


@dataclass(frozen=True)
class ChecklistRow:
    row_id: str
    pass_number: int
    label: str
    priority: str


@dataclass(frozen=True)
class FactEvidence:
    source_id: UUID
    source_code: str
    lineage_group: str
    quote_text: str
    retrieved_at: datetime


def normalize_source_url(url: str) -> str:
    """Canonicalize source URLs without discarding query parameters that may change content."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise DataLayerError("source URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise DataLayerError("source URL must not contain user credentials")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise DataLayerError("source URL has an invalid host or port") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def source_url_hash(url: str) -> str:
    return hashlib.sha256(normalize_source_url(url).encode("utf-8")).hexdigest()


def fingerprint_value(attribute_key: str, value: object) -> str:
    """Hash stable normalized JSON; object key order and string whitespace do not matter."""
    normalized_key = " ".join(attribute_key.casefold().split())

    def normalize(item: object) -> object:
        if isinstance(item, str):
            return " ".join(item.split())
        if isinstance(item, list):
            return [normalize(element) for element in item]
        if isinstance(item, dict):
            return {str(key): normalize(item[key]) for key in sorted(item)}
        return item

    try:
        payload = json.dumps(
            {"attribute_key": normalized_key, "value": normalize(value)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DataLayerError(f"fact value is not valid JSON: {exc}") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_checklist(path: Path = DEFAULT_CHECKLIST) -> tuple[str, list[ChecklistRow]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataLayerError(f"cannot load checklist config {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str):
        raise DataLayerError("checklist config requires a version string")
    raw_rows = raw.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) != 33:
        raise DataLayerError("checklist config must contain exactly 33 rows")
    rows: list[ChecklistRow] = []
    seen: set[str] = set()
    for item in raw_rows:
        if not isinstance(item, dict):
            raise DataLayerError("checklist rows must be mappings")
        row_id = item.get("id")
        pass_number = item.get("pass")
        label = item.get("row")
        priority = item.get("priority")
        if (
            not isinstance(row_id, str)
            or not re.fullmatch(r"P[12]\.\d{2}", row_id)
            or row_id in seen
        ):
            raise DataLayerError(f"invalid or duplicate checklist row id: {row_id!r}")
        if pass_number not in {1, 2} or not isinstance(label, str) or not label.strip():
            raise DataLayerError(f"invalid checklist row definition: {row_id}")
        if priority not in {"High", "Medium", "Optional"}:
            raise DataLayerError(f"invalid checklist priority for {row_id}")
        seen.add(row_id)
        rows.append(ChecklistRow(row_id, pass_number, label, priority))
    if sum(row.pass_number == 1 for row in rows) != 5:
        raise DataLayerError("checklist must contain five pass-1 rows")
    if sum(row.pass_number == 2 for row in rows) != 28:
        raise DataLayerError("checklist must contain twenty-eight pass-2 rows")
    return raw["version"], rows


def validate_coverage_proof(
    status: str,
    queries: list[str],
    tool_categories: list[str],
    result_count: int | None,
    blocked_reason: str | None = None,
) -> None:
    if status not in {"Done", "Partial", "Not done", "Blocked", "None"}:
        raise DataLayerError(f"invalid coverage status: {status}")
    if status == "Blocked":
        if not blocked_reason or not blocked_reason.strip():
            raise DataLayerError("Blocked coverage requires a reason")
        return
    if not queries or any(not query.strip() for query in queries):
        raise DataLayerError("coverage proof requires non-empty queries")
    if not tool_categories or any(not tool.strip() for tool in tool_categories):
        raise DataLayerError("coverage proof requires at least one tool category")
    if result_count is None or result_count < 0:
        raise DataLayerError("coverage proof requires a non-negative result count")


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
    url_hash = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
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


async def seed_coverage_rows(session: AsyncSession, run_id: UUID) -> int:
    """Seed all checklist rows from versioned config within the caller's tenant transaction."""
    version, rows = load_checklist()
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to seed coverage")
    run = (
        await session.execute(
            text(
                "SELECT account_id FROM account_runs "
                "WHERE tenant_id = :tenant_id AND id = :run_id FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "run_id": run_id},
        )
    ).one_or_none()
    if run is None:
        raise DataLayerError(f"run {run_id} is not visible in this tenant")
    await session.execute(
        text(
            "UPDATE account_runs SET checklist_version = :version, expected_coverage_rows = :count "
            "WHERE tenant_id = :tenant_id AND id = :run_id"
        ),
        {"version": version, "count": len(rows), "tenant_id": tenant_id, "run_id": run_id},
    )
    for row in rows:
        await session.execute(
            text(
                "INSERT INTO coverage_rows (tenant_id, account_id, run_id, checklist_version, "
                "row_key, pass, description, priority) VALUES (:tenant_id, :account_id, :run_id, "
                ":version, :row_key, :pass, :description, :priority) "
                "ON CONFLICT (tenant_id, run_id, row_key) DO NOTHING"
            ),
            {
                "tenant_id": tenant_id,
                "account_id": run.account_id,
                "run_id": run_id,
                "version": version,
                "row_key": row.row_id,
                "pass": row.pass_number,
                "description": row.label,
                "priority": row.priority,
            },
        )
    return len(rows)


async def record_coverage(
    session: AsyncSession,
    *,
    run_id: UUID,
    row_key: str,
    status: CoverageStatus,
    queries: list[str] | None = None,
    tool_categories: list[str] | None = None,
    result_count: int | None = None,
    blocked_reason: str | None = None,
) -> None:
    queries = queries or []
    tool_categories = tool_categories or []
    validate_coverage_proof(status, queries, tool_categories, result_count, blocked_reason)
    tenant_id = (await session.execute(text("SELECT app_tenant()"))).scalar_one_or_none()
    if tenant_id is None:
        raise DataLayerError("tenant context is required to update coverage")
    result = await session.execute(
        text(
            "UPDATE coverage_rows SET status = :status, queries = CAST(:queries AS jsonb), "
            "tool_categories = CAST(:tool_categories AS jsonb), result_count = :result_count, "
            "blocked_reason = :blocked_reason, last_checked_at = now() "
            "WHERE tenant_id = :tenant_id AND run_id = :run_id AND row_key = :row_key"
        ),
        {
            "status": status,
            "queries": json.dumps(queries),
            "tool_categories": json.dumps(tool_categories),
            "result_count": result_count,
            "blocked_reason": blocked_reason,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "row_key": row_key,
        },
    )
    if result.rowcount != 1:
        raise DataLayerError(f"coverage row {row_key} is not seeded for this run")


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
