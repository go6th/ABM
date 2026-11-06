"""The company gate on an uploaded workspace package (2026-11-05). SYNTHETIC data only.

A workspace zip names its own company (``documents.workspace_identity``). Uploaded to an account
with another domain, the upload is HELD and the user is asked: "Yes, same company" binds the
package's domain to the account and the import runs, so the package's postings count as the
account; "No" (the ordinary confirm-company call with the package's company) moves the file to
that company's account. Until then nothing is imported. The UCB bundle of 2026-11-04 was built
without this gate: every posting of careers.ucb.com came out "Not the account".

GUARANTEE(test_a_workspace_naming_another_domain_is_held_and_nothing_is_imported)
GUARANTEE(test_same_company_binds_the_domain_and_the_postings_count_as_the_account)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from documents import repo
from documents.service import DocumentError
from tests.conftest import ADMIN_DSN, _make_account
from tests.documents import workspace_zip_owner as OWNER
from tests.documents.world import service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _count(table: str, account: UUID) -> int:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        return int(
            c.execute(
                f'SELECT count(*) FROM "{table}" WHERE account_id = %s',  # noqa: S608 catalog name
                (account,),
            ).fetchone()[0]  # type: ignore[index]
        )


def _watch(account: UUID) -> dict:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        row = c.execute(
            "SELECT watch_config_json FROM accounts WHERE id = %s", (account,)
        ).fetchone()
    value = row[0] if row else {}
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def _held(tenant: UUID, document: UUID) -> dict:
    async with tenant_txn(tenant) as s:
        doc = await repo.get(s, document)
    return {"status": doc.status, "company": dict(doc.detected_company or {})}


async def test_a_workspace_naming_another_domain_is_held_and_nothing_is_imported(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, OWNER.OTHER_ACCOUNT_DOMAIN)
    svc = service(tmp_path)  # the lenient rule: the gate holds on the package's own evidence
    done = await upload(svc, tenant_a, OWNER.ZIP_NAME, OWNER.workspace_zip_bytes(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "needs_account"
    held = await _held(tenant_a, document)
    assert held["status"] == "needs_account"
    ws = held["company"]["workspace"]
    assert ws["decision"] == "mismatch"
    assert ws["domain"] == OWNER.WORKSPACE_DOMAIN
    assert ws["name"] == OWNER.ACCOUNT_NAME
    assert ws["account_domain"] == OWNER.OTHER_ACCOUNT_DOMAIN
    assert ws["research_date"] == OWNER.RESEARCH_DATE
    assert held["company"]["reason"] == "workspace_other_company"
    # the status view says which company the file looks like, for the prompt
    view = await svc.status(tenant_a, document)
    assert view["needs_company"] is True
    assert view["company"]["company"]["domain"] == OWNER.WORKSPACE_DOMAIN
    # nothing of the package reached the account's tables
    for table in ("job_postings", "people", "tech_detections", "news_events", "sources"):
        assert _count(table, account) == 0, table


async def test_same_company_binds_the_domain_and_the_postings_count_as_the_account(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, OWNER.OTHER_ACCOUNT_DOMAIN)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, OWNER.ZIP_NAME, OWNER.workspace_zip_bytes(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "needs_account"

    view = await svc.same_company(tenant_a, document)
    assert view["status"] == "parsed"
    watch = _watch(account)
    assert OWNER.WORKSPACE_DOMAIN in watch["domains"]
    assert OWNER.ACCOUNT_NAME in watch["aliases"]
    assert OWNER.CAREERS_HOST in watch["careers_hosts"]
    held = await _held(tenant_a, document)
    assert held["company"]["workspace"]["decision"] == "same"
    # a second answer is refused: the file is no longer waiting
    with pytest.raises(DocumentError):
        await svc.same_company(tenant_a, document)

    # the queued read now imports: the postings are the account's own in the repair stage
    assert await svc.process(tenant_a, document) == "indexed"
    assert _count("job_postings", account) > 0
    from agents.config import load_skill_config
    from exports.repair import pipeline as RP
    from exports.repair import readers as RD
    from exports.repair.profile import load_profile

    async with tenant_txn(tenant_a) as s:
        profile = await load_profile(
            s, account, "test-owner", datetime.now(UTC), load_skill_config().scope
        )
        inp = await RD.load_input(
            s, profile, account, "test-owner", payload=svc.payloads.get, tracker_labels={}
        )
    assert OWNER.WORKSPACE_DOMAIN in profile.domains
    assert OWNER.CAREERS_HOST in profile.careers_hosts
    tables = RP.repair_tables(profile, inp)
    jobs = tables["jobs"]
    assert jobs.stats["account_platform_postings"] > 0
    assert not jobs.stats["non_account_entities"], jobs.stats["non_account_entities"]
    checks = {c.name: c for c in RP.repair(profile, inp).checks}
    assert checks["entity_resolves_account"].passed, checks["entity_resolves_account"].detail


async def test_no_moves_the_file_to_the_company_the_package_names(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, OWNER.OTHER_ACCOUNT_DOMAIN)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, OWNER.ZIP_NAME, OWNER.workspace_zip_bytes(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "needs_account"
    # "No, it belongs to Octavia Bio": the ordinary confirm step with the package's company
    from account_intelligence.runs import lifecycle

    async with tenant_txn(tenant_a) as s:
        other, created = await lifecycle.add_account(
            s, tenant_a, OWNER.WORKSPACE_DOMAIN, added_by="test-owner"
        )
        await lifecycle.name_account(s, other, OWNER.ACCOUNT_NAME)
        await s.execute(
            text("UPDATE accounts SET owner_user_id = 'test-owner' WHERE id = :a"), {"a": other}
        )
    assert created
    await svc.confirm_company(tenant_a, [document], other)
    async with tenant_txn(tenant_a) as s:
        doc = await repo.get(s, document)
    assert str(doc.account_id) == str(other)
    assert doc.status == "parsed"
    assert await svc.process(tenant_a, document) == "indexed"
    assert _count("job_postings", other) > 0
    assert _count("job_postings", account) == 0
    assert _watch(account).get("domains", []) == []


def test_the_owner_shaped_zip_is_synthetic() -> None:
    names = OWNER.workspace_zip_bytes()
    assert b"ucb" not in names.lower()
    assert str(uuid4())  # the fixture carries no real identifiers
