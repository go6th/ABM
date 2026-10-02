"""Tenant isolation and integrity checks for data-layer v1 tables."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.db.data_layer import (
    DataLayerError,
    FactEvidence,
    create_fact_with_evidence,
    get_or_create_person,
    get_or_create_source,
    merge_person,
    record_coverage,
    upsert_job_posting,
    upsert_tech_detection,
)
from src.db.session import tenant_txn
from src.storage.raw_payloads import LocalRawPayloadStore, get_or_store_payload
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


def _seed_tenant_b_data(tenant_id: UUID, account_id: UUID, run_id: UUID) -> tuple[UUID, UUID]:
    with psycopg.connect(ADMIN_DSN) as connection:
        connection.execute(
            "INSERT INTO source_code_counters (tenant_id, account_id, next_value) "
            "VALUES (%s, %s, 2)",
            (tenant_id, account_id),
        )
        source_id = connection.execute(
            "INSERT INTO sources (tenant_id, account_id, source_code, url, url_hash, domain, "
            "tier, lineage_group) VALUES (%s, %s, 'S-0001', 'https://example.com/evidence', "
            "%s, 'example.com', 'T1', 'fixture-lineage') RETURNING id",
            (tenant_id, account_id, "1" * 64),
        ).fetchone()[0]
        fact_id = connection.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint, "
            "visibility) VALUES (%s, %s, 'fixture.fact', '{}', %s, 'public') RETURNING id",
            (tenant_id, account_id, "2" * 64),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO fact_evidence (tenant_id, account_id, fact_id, source_id, source_code, "
            "lineage_group, quote_text) VALUES (%s, %s, %s, %s, 'S-0001', 'fixture-lineage', "
            "'Recorded fixture evidence')",
            (tenant_id, account_id, fact_id, source_id),
        )
        connection.execute(
            "INSERT INTO people (tenant_id, account_id, name, normalized_name, title) "
            "VALUES (%s, %s, 'Fixture Person', 'fixture person', 'Synthetic title')",
            (tenant_id, account_id),
        )
        connection.execute(
            "INSERT INTO job_postings (tenant_id, account_id, posting_url, posting_url_hash, "
            "title, posting_class) VALUES (%s, %s, 'https://example.com/jobs/1', %s, "
            "'Synthetic role', 'build')",
            (tenant_id, account_id, "3" * 64),
        )
        connection.execute(
            "INSERT INTO tech_detections (tenant_id, account_id, technology, evidence_url, "
            "evidence_hash, evidence_text) VALUES (%s, %s, 'Synthetic Tool', "
            "'https://example.com/tech', %s, 'synthetic evidence')",
            (tenant_id, account_id, "4" * 64),
        )
        connection.execute(
            "INSERT INTO raw_payloads (tenant_id, account_id, run_id, args_hash, object_key, "
            "content_hash, provider_code) VALUES (%s, %s, %s, %s, %s, %s, 'p_fixture')",
            (
                tenant_id,
                account_id,
                run_id,
                "5" * 64,
                f"{tenant_id}/{account_id}/{run_id}/{'6' * 64}.json.gz",
                "6" * 64,
            ),
        )
        connection.execute(
            "INSERT INTO tool_call_intents (tenant_id, run_id, tool, args_hash, intent_token, "
            "lease_expires_at) VALUES (%s, %s, 'fixture', %s, %s, now() + interval '15 minutes')",
            (tenant_id, run_id, "a" * 64, uuid4()),
        )
    return source_id, fact_id


async def test_new_data_tables_are_tenant_isolated(
    tenant_a: UUID, tenant_b: UUID, account_of_b: UUID, run_of_b: UUID
) -> None:
    _seed_tenant_b_data(tenant_b, account_of_b, run_of_b)
    tables = (
        "source_code_counters",
        "sources",
        "fact_evidence",
        "coverage_rows",
        "people",
        "job_postings",
        "tech_detections",
        "raw_payloads",
        "tool_call_intents",
    )
    async with tenant_txn(tenant_a) as session:
        for table in tables:
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_b},
                )
            ).scalar_one()
            assert count == 0, f"tenant A saw tenant B rows in {table}"


async def test_stable_source_codes_are_unique_contiguous_and_reused(
    tenant_a: UUID,
) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"source-{uuid4().hex}.example.com"),
        ).fetchone()[0]

    async def allocate(index: int) -> tuple[str, UUID]:
        async with tenant_txn(tenant_a) as session:
            return await get_or_create_source(
                session, account_id, f"https://example.com/articles/{index}"
            )

    allocations = await asyncio.gather(*(allocate(index) for index in range(50)))
    codes = [code for code, _ in allocations]
    assert len(set(codes)) == 50
    assert sorted(codes) == [f"S-{index:04d}" for index in range(1, 51)]
    async with tenant_txn(tenant_a) as session:
        refreshed = await get_or_create_source(
            session, account_id, "HTTPS://EXAMPLE.COM:443/articles/1#fragment"
        )
    assert refreshed == allocations[1]


async def test_source_identity_cannot_be_deleted(tenant_a: UUID) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"immutable-{uuid4().hex}.example.com"),
        ).fetchone()[0]
    async with tenant_txn(tenant_a) as session:
        _, source_id = await get_or_create_source(
            session, account_id, "https://example.com/retained"
        )

    with (
        psycopg.connect(ADMIN_DSN, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="never reused"),
    ):
        connection.execute("DELETE FROM sources WHERE id = %s", (source_id,))


async def test_fact_writer_commits_fact_and_evidence_together(tenant_a: UUID) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"fact-{uuid4().hex}.example.com"),
        ).fetchone()[0]
    async with tenant_txn(tenant_a) as session:
        source_code, source_id = await get_or_create_source(
            session, account_id, "https://example.com/fact-source"
        )
        lineage_group = (
            await session.execute(
                text("SELECT lineage_group FROM sources WHERE id = :source_id"),
                {"source_id": source_id},
            )
        ).scalar_one()
        with pytest.raises(DataLayerError, match="requires at least one evidence"):
            await create_fact_with_evidence(
                session,
                account_id=account_id,
                attribute_key="company.name",
                value="Synthetic Example",
                engine_grade="B",
                product_grade="probable",
                confidence=0.8,
                visibility="public",
                read_in_primary_this_run=True,
                evidence=[],
            )
        with pytest.raises(DataLayerError, match="Confirmed fact"):
            await create_fact_with_evidence(
                session,
                account_id=account_id,
                attribute_key="company.name",
                value="Synthetic Example",
                engine_grade="A",
                product_grade="confirmed",
                confidence=1.0,
                visibility="public",
                read_in_primary_this_run=False,
                evidence=[
                    FactEvidence(
                        source_id=source_id,
                        source_code=source_code,
                        lineage_group=lineage_group,
                        quote_text="Synthetic supporting quote.",
                        retrieved_at=datetime.now(UTC),
                    )
                ],
            )
        fact_id = await create_fact_with_evidence(
            session,
            account_id=account_id,
            attribute_key="company.name",
            value="Synthetic Example",
            engine_grade="B",
            product_grade="probable",
            confidence=0.8,
            visibility="public",
            read_in_primary_this_run=True,
            evidence=[
                FactEvidence(
                    source_id=source_id,
                    source_code=source_code,
                    lineage_group=lineage_group,
                    quote_text="Synthetic Example is named in this fixture.",
                    retrieved_at=datetime.now(UTC),
                )
            ],
        )
        evidence_count = (
            await session.execute(
                text("SELECT count(*) FROM fact_evidence WHERE fact_id = :fact_id"),
                {"fact_id": fact_id},
            )
        ).scalar_one()
    assert evidence_count == 1
    with pytest.raises(DataLayerError, match="must match the referenced source"):
        async with tenant_txn(tenant_a) as session:
            await create_fact_with_evidence(
                session,
                account_id=account_id,
                attribute_key="company.name",
                value="Unsupported Source Label",
                engine_grade="B",
                product_grade="probable",
                confidence=0.8,
                visibility="public",
                read_in_primary_this_run=True,
                evidence=[
                    FactEvidence(
                        source_id=source_id,
                        source_code="S-9999",
                        lineage_group=lineage_group,
                        quote_text="Synthetic but mismatched source metadata.",
                        retrieved_at=datetime.now(UTC),
                    )
                ],
            )
    with pytest.raises(DBAPIError, match="fact values are append-only"):
        async with tenant_txn(tenant_a) as session:
            await session.execute(
                text("UPDATE facts SET attribute_key = 'rewritten' WHERE id = :fact_id"),
                {"fact_id": fact_id},
            )
    async with tenant_txn(tenant_a) as session:
        await session.execute(
            text("UPDATE facts SET superseded_at = now() WHERE id = :fact_id"),
            {"fact_id": fact_id},
        )
    with pytest.raises(DBAPIError, match="superseded fact cannot be changed again"):
        async with tenant_txn(tenant_a) as session:
            await session.execute(
                text(
                    "UPDATE facts SET superseded_at = now() + interval '1 second' "
                    "WHERE id = :fact_id"
                ),
                {"fact_id": fact_id},
            )


async def test_database_rejects_fact_without_evidence(tenant_a: UUID) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"no-evidence-{uuid4().hex}.example.com"),
        ).fetchone()[0]

    with (
        psycopg.connect(ADMIN_DSN, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="requires at least one evidence"),
    ):
        connection.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, "
            "fingerprint, visibility) VALUES (%s, %s, 'unsupported.claim', '{}', %s, 'public')",
            (tenant_a, account_id, "d" * 64),
        )


async def test_people_dedupe_by_profile_then_name_and_keep_merge_pointer(
    tenant_a: UUID,
) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"people-{uuid4().hex}.example.com"),
        ).fetchone()[0]

    async with tenant_txn(tenant_a) as session:
        first = await get_or_create_person(
            session,
            account_id=account_id,
            name="Example Executive",
            title="Chief Executive Officer",
            profile_url="HTTPS://PEOPLE.EXAMPLE/exec#profile",
            level="C-suite",
            priority="A",
        )
        same_profile = await get_or_create_person(
            session,
            account_id=account_id,
            name="Updated Executive Name",
            title="Chief Executive Officer",
            profile_url="https://people.example/exec",
        )
        no_profile = await get_or_create_person(
            session,
            account_id=account_id,
            name="Unlinked Executive",
            title="VP",
        )
        same_name = await get_or_create_person(
            session,
            account_id=account_id,
            name="  UNLINKED   EXECUTIVE ",
            title="VP",
        )
        distinct_profile_same_name = await get_or_create_person(
            session,
            account_id=account_id,
            name="Unlinked Executive",
            title="VP",
            profile_url="https://people.example/another",
        )
        assert first == same_profile
        assert no_profile == same_name
        assert distinct_profile_same_name == no_profile
        profile_hash = (
            await session.execute(
                text("SELECT profile_url_hash FROM people WHERE tenant_id = :tenant AND id = :id"),
                {"tenant": tenant_a, "id": no_profile},
            )
        ).scalar_one()
        assert profile_hash is not None

        with pytest.raises(DataLayerError, match="exclusion reason"):
            await get_or_create_person(
                session,
                account_id=account_id,
                name="Excluded Executive",
                title="VP",
                excluded=True,
            )
        with pytest.raises(DataLayerError, match="priority"):
            await get_or_create_person(
                session,
                account_id=account_id,
                name="Invalid Priority",
                title="VP",
                priority="D",
            )
        await merge_person(
            session,
            account_id=account_id,
            source_person_id=no_profile,
            target_person_id=first,
        )
        merged_to = (
            await session.execute(
                text("SELECT merged_into FROM people WHERE tenant_id = :tenant AND id = :id"),
                {"tenant": tenant_a, "id": no_profile},
            )
        ).scalar_one()
        assert merged_to == first
    with (
        psycopg.connect(ADMIN_DSN, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="people rows are retained"),
    ):
        connection.execute("DELETE FROM people WHERE id = %s", (no_profile,))

    with pytest.raises(DataLayerError, match="cannot be merged into itself"):
        async with tenant_txn(tenant_a) as session:
            await merge_person(
                session,
                account_id=account_id,
                source_person_id=first,
                target_person_id=first,
            )


async def test_posting_upsert_preserves_full_text_read_and_normalizes_url(
    tenant_a: UUID,
) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"jobs-{uuid4().hex}.example.com"),
        ).fetchone()[0]
    async with tenant_txn(tenant_a) as session:
        posting_id = await upsert_job_posting(
            session,
            account_id=account_id,
            posting_url="HTTPS://JOBS.EXAMPLE/role/1#details",
            title="Staff Engineer",
            posting_class="build",
            platform_sentence="Builds the platform.",
            full_text="Complete synthetic job description.",
            full_text_read=True,
        )
        repeated_id = await upsert_job_posting(
            session,
            account_id=account_id,
            posting_url="https://jobs.example/role/1",
            title="Principal Engineer",
            posting_class="admin_owner",
            platform_sentence=None,
            full_text=None,
            full_text_read=False,
        )
        details = (
            await session.execute(
                text(
                    "SELECT title, platform_sentence, full_text, full_text_read "
                    "FROM job_postings WHERE tenant_id = :tenant AND id = :id"
                ),
                {"tenant": tenant_a, "id": posting_id},
            )
        ).one()
        assert posting_id == repeated_id
        assert details.title == "Principal Engineer"
        assert details.platform_sentence == "Builds the platform."
        assert details.full_text == "Complete synthetic job description."
        assert details.full_text_read is True

        with pytest.raises(DataLayerError, match="invalid job posting class"):
            await upsert_job_posting(
                session,
                account_id=account_id,
                posting_url="https://jobs.example/invalid",
                title="Role",
                posting_class="other",
                platform_sentence=None,
                full_text=None,
                full_text_read=False,
            )
        with pytest.raises(DataLayerError, match="requires the full posting text"):
            await upsert_job_posting(
                session,
                account_id=account_id,
                posting_url="https://jobs.example/invalid-full-text",
                title="Role",
                posting_class="build",
                platform_sentence=None,
                full_text=None,
                full_text_read=True,
            )


async def test_technology_detection_stays_lead_until_corroborated(
    tenant_a: UUID,
) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
        account_id = connection.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_a, f"tech-{uuid4().hex}.example.com"),
        ).fetchone()[0]
    first_seen = datetime(2026, 9, 1, tzinfo=UTC)
    last_seen = datetime(2026, 9, 2, tzinfo=UTC)
    async with tenant_txn(tenant_a) as session:
        detection_id = await upsert_tech_detection(
            session,
            account_id=account_id,
            technology="Synthetic CRM",
            evidence_url="https://example.com/technology#details",
            evidence_text=" Synthetic evidence   with spaces ",
            first_seen_at=first_seen,
            last_seen_at=last_seen,
        )
        same_detection = await upsert_tech_detection(
            session,
            account_id=account_id,
            technology="Synthetic CRM",
            evidence_url="https://example.com/technology",
            evidence_text="Synthetic evidence with spaces",
            first_seen_at=first_seen,
            last_seen_at=datetime(2026, 9, 4, tzinfo=UTC),
            cross_check_result="corroborated",
            cross_checked_at=datetime(2026, 9, 5, tzinfo=UTC),
            product_grade="probable",
        )
        assert detection_id == same_detection
        stored = (
            await session.execute(
                text(
                    "SELECT last_seen_at, cross_check_result, product_grade "
                    "FROM tech_detections WHERE tenant_id = :tenant AND id = :id"
                ),
                {"tenant": tenant_a, "id": detection_id},
            )
        ).one()
        assert stored.last_seen_at == datetime(2026, 9, 4, tzinfo=UTC)
        assert stored.cross_check_result == "corroborated"
        assert stored.product_grade == "probable"

        with pytest.raises(DataLayerError, match="must remain a Lead"):
            await upsert_tech_detection(
                session,
                account_id=account_id,
                technology="Unverified Tool",
                evidence_url="https://example.com/unverified",
                evidence_text="Date range only",
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                product_grade="confirmed",
            )
        with pytest.raises(DataLayerError, match="must not precede"):
            await upsert_tech_detection(
                session,
                account_id=account_id,
                technology="Invalid Dates",
                evidence_url="https://example.com/invalid-date",
                evidence_text="Synthetic",
                first_seen_at=last_seen,
                last_seen_at=first_seen,
            )


async def test_raw_payload_is_pulled_once_per_run_and_args(
    tenant_b: UUID, account_of_b: UUID, run_of_b: UUID, tmp_path
) -> None:
    account_id = account_of_b
    store = LocalRawPayloadStore(tmp_path)
    fetch_count = 0

    async def fetch():
        nonlocal fetch_count
        fetch_count += 1
        return {"records": [{"value": "synthetic"}]}

    stored = []
    for _ in range(2):
        async with tenant_txn(tenant_b) as session:
            stored.append(
                await get_or_store_payload(
                    session,
                    store,
                    account_id=account_id,
                    run_id=run_of_b,
                    args_hash="e" * 64,
                    provider_code="p_fixture",
                    fetch=fetch,
                )
            )
    assert fetch_count == 1
    assert stored[0] == stored[1]
    with psycopg.connect(ADMIN_DSN) as connection:
        provider_code = connection.execute(
            "SELECT provider_code FROM raw_payloads WHERE tenant_id = %s AND run_id = %s",
            (tenant_b, run_of_b),
        ).fetchone()[0]
    assert provider_code == "p_fixture"
    assert "fixture" not in stored[0].object_key


async def test_new_table_cross_tenant_writes_are_rejected(
    tenant_a: UUID,
    tenant_b: UUID,
    account_of_b: UUID,
    run_of_b: UUID,
) -> None:
    source_id, fact_id = _seed_tenant_b_data(tenant_b, account_of_b, run_of_b)
    statements = (
        (
            "INSERT INTO source_code_counters (tenant_id, account_id) VALUES (:tenant, :account)",
            {"tenant": tenant_b, "account": account_of_b},
        ),
        (
            "INSERT INTO sources (tenant_id, account_id, source_code, url, url_hash, domain, "
            "tier, lineage_group) VALUES (:tenant, :account, 'S-9999', 'https://other.test', "
            ":hash, 'other.test', 'T4', 'x')",
            {"tenant": tenant_b, "account": account_of_b, "hash": "7" * 64},
        ),
        (
            "INSERT INTO fact_evidence (tenant_id, account_id, fact_id, source_id, source_code, "
            "lineage_group, quote_text) VALUES (:tenant, :account, :fact, :source, 'S-0001', "
            "'x', 'x')",
            {"tenant": tenant_b, "account": account_of_b, "fact": fact_id, "source": source_id},
        ),
        (
            "INSERT INTO coverage_rows (tenant_id, account_id, run_id, checklist_version, row_key, "
            "pass, description, priority) VALUES (:tenant, :account, :run, '1.1.0', 'P2.99', "
            "2, 'test row', 'Optional')",
            {"tenant": tenant_b, "account": account_of_b, "run": run_of_b},
        ),
        (
            "INSERT INTO people (tenant_id, account_id, name, normalized_name, title) "
            "VALUES (:tenant, :account, 'Other Person', 'other person', 'Role')",
            {"tenant": tenant_b, "account": account_of_b},
        ),
        (
            "INSERT INTO job_postings (tenant_id, account_id, posting_url, posting_url_hash, "
            "title, posting_class) VALUES (:tenant, :account, 'https://other.test/jobs/1', "
            ":hash, 'Role', 'build')",
            {"tenant": tenant_b, "account": account_of_b, "hash": "8" * 64},
        ),
        (
            "INSERT INTO tech_detections (tenant_id, account_id, technology, evidence_url, "
            "evidence_hash, evidence_text) VALUES (:tenant, :account, 'Other', "
            "'https://other.test', :hash, 'evidence')",
            {"tenant": tenant_b, "account": account_of_b, "hash": "9" * 64},
        ),
        (
            "INSERT INTO raw_payloads (tenant_id, account_id, run_id, args_hash, object_key, "
            "content_hash, provider_code) VALUES (:tenant, :account, :run, :args, :key, :hash, "
            "'p_fixture')",
            {
                "tenant": tenant_b,
                "account": account_of_b,
                "run": run_of_b,
                "args": "a" * 64,
                "key": f"{tenant_b}/{account_of_b}/{run_of_b}/{'b' * 64}.json.gz",
                "hash": "b" * 64,
            },
        ),
        (
            "INSERT INTO tool_call_intents (tenant_id, run_id, tool, args_hash, intent_token, "
            "lease_expires_at) VALUES (:tenant, :run, 'test', :hash, :token, "
            "now() + interval '1 minute')",
            {
                "tenant": tenant_b,
                "run": run_of_b,
                "hash": "c" * 64,
                "token": uuid4(),
            },
        ),
    )
    for statement, params in statements:
        with pytest.raises(Exception, match="row-level security"):
            async with tenant_txn(tenant_a) as session:
                await session.execute(text(statement), params)


async def test_high_coverage_blocks_completion_until_proved(tenant_b: UUID, run_of_b: UUID) -> None:
    with pytest.raises(Exception, match="High priority coverage"):
        async with tenant_txn(tenant_b) as session:
            await session.execute(
                text("UPDATE account_runs SET status = 'COMPLETED' WHERE id = :run_id"),
                {"run_id": run_of_b},
            )

    async with tenant_txn(tenant_b) as session:
        high_rows = (
            await session.execute(
                text(
                    "SELECT row_key FROM coverage_rows WHERE run_id = :run_id AND priority = 'High'"
                ),
                {"run_id": run_of_b},
            )
        ).all()
        for row in high_rows:
            await record_coverage(
                session,
                run_id=run_of_b,
                row_key=row.row_key,
                status="Blocked",
                blocked_reason="Synthetic test fixture: required input unavailable",
            )
        await session.execute(
            text("UPDATE account_runs SET status = 'COMPLETED' WHERE id = :run_id"),
            {"run_id": run_of_b},
        )
