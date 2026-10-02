"""Accounts table rules (data layer v1, step 7).

Constraint tests run raw SQL as admin so the database itself is proven to
reject bad rows, not only the Python model.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from account_intelligence.db.models import Account
from tests.db.conftest import make_account, make_tenant, set_tenant

DOMAIN_VIOLATIONS = [
    ("https://bigco.example.com", "scheme"),
    ("bigco.example.com/path", "path"),
    ("bigco.example.com:443", "port"),
    ("BigCo.example.com", "uppercase"),
    ("www.bigco.example.com", "www"),
    ("localhost", "no-dot"),
    ("   ", "blank"),
    ("bigco .example.com", "space"),
]


def test_valid_account_roundtrip_via_model(db, admin) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    set_tenant(db, tenant)  # RLS: the write must carry the tenant context
    account = Account(
        tenant_id=tenant,
        domain="bigco.example.com",
        name="BigCo (synthetic)",
        industry="software",
        primary_lane="salesforce",
        pursuing_client="client-west",
    )
    db.add(account)
    db.flush()
    assert account.id is not None
    assert account.created_at is not None
    assert account.updated_at is not None


@pytest.mark.parametrize("bad_domain", DOMAIN_VIOLATIONS, ids=lambda v: v[1])
def test_domain_shape_is_enforced_by_the_database(admin, bad_domain) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    with pytest.raises(Exception, match="ck_accounts_domain_shape"):
        make_account(admin, tenant, bad_domain[0])


def test_domain_must_be_unique_per_tenant_but_not_globally(admin) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    make_account(admin, tenant, "bigco.example.com")
    with pytest.raises(Exception, match="uq_accounts_tenant_id_domain"):
        make_account(admin, tenant, "bigco.example.com")
    other = make_tenant(admin)
    make_account(admin, other, "bigco.example.com")  # different tenant: fine


def test_tenant_id_is_required(admin) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(Exception, match="tenant_id"):
        admin.execute("INSERT INTO accounts (domain) VALUES ('bigco.example.com')")


def test_tenant_name_index_exists(admin) -> None:  # type: ignore[no-untyped-def]
    rows = admin.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'accounts'").fetchall()
    names = {row[0] for row in rows}
    assert "ix_accounts_tenant_id_name" in names
    assert "uq_accounts_tenant_id_domain" in names


def test_timestamps_are_set_by_the_database(admin) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    account_id = make_account(admin, tenant, "bigco.example.com")
    row = admin.execute(
        "SELECT created_at, updated_at, careers_url_verified_at FROM accounts WHERE id = %s",
        (account_id,),
    ).fetchone()
    assert row is not None
    assert row[0] is not None and row[1] is not None
    assert row[2] is None  # verification date only set after a real visit


def test_model_maps_the_migrated_table(db, admin) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    account_id = make_account(admin, tenant, "bigco.example.com")
    set_tenant(db, tenant)
    found = db.execute(select(Account).where(Account.id == account_id)).scalar_one()
    assert found.domain == "bigco.example.com"
    assert found.watch_config_json == {}
    assert found.name is None


def test_tenant_id_defaults_to_the_transaction_tenant(db, admin) -> None:  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    set_tenant(db, tenant)
    account = Account(domain="defaulted.example.com")
    db.add(account)
    db.flush()
    db.refresh(account)
    assert account.tenant_id == tenant


def test_rls_rejects_an_account_for_another_tenant(db, admin) -> None:  # type: ignore[no-untyped-def]
    mine, other = make_tenant(admin), make_tenant(admin)
    set_tenant(db, mine)
    db.add(Account(tenant_id=other, domain="foreign.example.com"))
    with pytest.raises(Exception, match="row-level security"):
        db.flush()
