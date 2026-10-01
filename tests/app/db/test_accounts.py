"""intel.accounts: domain normalisation, the default tenant, and every constraint."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg.errors import CheckViolation, NotNullViolation, UniqueViolation
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import DEFAULT_TENANT_ID
from app.db.models import Account
from app.db.models.account import InvalidCareersUrl, InvalidDomain, normalize_domain
from app.db.url import sqlalchemy_url
from tests.app.db.helpers import check_constraint_names, constraint_holds

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

# --------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "example.com"),
        ("Example.COM", "example.com"),
        ("https://WWW.Example.com:443/careers?x=1#top", "example.com"),
        ("http://user:pw@shop.example.co.uk/", "shop.example.co.uk"),
        ("www.example.com.", "example.com"),
        ("  example.com/path  ", "example.com"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("www2.example.com", "www2.example.com"),
    ],
)
def test_normalize_domain(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "localhost", "exa mple.com", "-bad.example", "http://", "example..com", "a_b.com"],
)
def test_normalize_domain_rejects(raw: str) -> None:
    with pytest.raises(InvalidDomain):
        normalize_domain(raw)


def test_model_normalises_domain_on_assignment() -> None:
    a = Account(name="Acme", domain="HTTPS://www.Acme.com/about")
    assert a.domain == "acme.com"
    with pytest.raises(InvalidDomain):
        Account(name="Acme", domain="not a domain")


def test_careers_url_is_recorded_with_its_verification_time() -> None:
    a = Account(name="Acme", domain="acme.com")
    a.record_careers_url(" https://careers.acme.com/jobs ", NOW)
    assert a.careers_url == "https://careers.acme.com/jobs"
    assert a.careers_url_verified_at == NOW
    for bad in ("acme.com/careers", "ftp://acme.com", "https://"):
        with pytest.raises(InvalidCareersUrl):
            a.record_careers_url(bad, NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        a.careers_url_verified_at = datetime(2026, 10, 1)


# --------------------------------------------------------------------- database


@pytest.fixture()
def engine(migrated_database_url: str) -> Iterator[Engine]:
    eng = create_engine(sqlalchemy_url(migrated_database_url))
    yield eng
    eng.dispose()


def _insert(conn: Connection, **values: Any) -> None:
    row = {"name": "Acme", "domain": "acme.com", **values}
    cols = ", ".join(row)
    params = ", ".join(f":{k}" for k in row)
    conn.execute(text(f"INSERT INTO intel.accounts ({cols}) VALUES ({params})"), row)  # noqa: S608


def _violation(engine: Engine, **values: Any) -> BaseException:
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        _insert(conn, **values)
    assert info.value.orig is not None
    return info.value.orig


def test_orm_insert_gets_default_tenant_and_conventions(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        a = Account(name="Acme", domain="www.ACME.com", primary_lane="veeva")
        s.add(a)
        s.flush()
        assert a.tenant_id == DEFAULT_TENANT_ID
        assert isinstance(a.id, uuid.UUID)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT tenant_id, domain, created_at FROM intel.accounts")).one()
    assert row.tenant_id == DEFAULT_TENANT_ID
    assert row.domain == "acme.com"
    assert row.created_at.tzinfo is not None


def test_raw_insert_without_tenant_gets_the_default(engine: Engine) -> None:
    with engine.begin() as conn:
        _insert(conn)
        tenant = conn.execute(text("SELECT tenant_id FROM intel.accounts")).scalar_one()
    assert tenant == DEFAULT_TENANT_ID


def test_unique_tenant_and_domain(engine: Engine) -> None:
    with engine.begin() as conn:
        _insert(conn)
        _insert(conn, tenant_id=uuid.uuid4())  # same domain, other tenant: allowed
    err = _violation(engine)
    assert isinstance(err, UniqueViolation)
    assert err.diag.constraint_name == "uq_accounts_tenant_id_domain"


def test_normalisation_and_uniqueness_work_together(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        s.add(Account(name="Acme", domain="acme.com"))
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        s.add(Account(name="Acme Corp", domain="https://WWW.acme.com/careers"))


@pytest.mark.parametrize("column", ["name", "domain"])
def test_required_columns(engine: Engine, column: str) -> None:
    assert isinstance(_violation(engine, **{column: None}), NotNullViolation)


def _domain(v: str) -> dict[str, tuple[Any, str]]:
    return {"domain": (v, "text")}


def _careers(url: str | None, at: datetime | None) -> dict[str, tuple[Any, str]]:
    return {"careers_url": (url, "text"), "careers_url_verified_at": (at, "timestamptz")}


CHECKS: list[tuple[str, dict[str, tuple[Any, str]], dict[str, tuple[Any, str]]]] = [
    ("ck_accounts_name_not_blank", {"name": ("Acme", "text")}, {"name": ("  ", "text")}),
    ("ck_accounts_domain_lowercase", _domain("acme.com"), _domain("Acme.com")),
    ("ck_accounts_domain_no_scheme", _domain("acme.com"), _domain("https:acme.com://")),
    ("ck_accounts_domain_no_slash", _domain("acme.com"), _domain("acme.com/careers")),
    ("ck_accounts_domain_no_www", _domain("wwwacme.com"), _domain("www.acme.com")),
    ("ck_accounts_domain_hostname", _domain("a-b.acme.co.uk"), _domain("acme")),
    (
        "ck_accounts_careers_url_http",
        {"careers_url": ("https://acme.com/jobs", "text")},
        {"careers_url": ("acme.com/jobs", "text")},
    ),
    (
        "ck_accounts_careers_url_verified_together",
        {
            "careers_url": ("https://acme.com", "text"),
            "careers_url_verified_at": (NOW, "timestamptz"),
        },
        {
            "careers_url": ("https://acme.com", "text"),
            "careers_url_verified_at": (None, "timestamptz"),
        },
    ),
    (
        "ck_accounts_pursuing_client_not_blank",
        {"pursuing_client": ("Client Co", "text")},
        {"pursuing_client": ("", "text")},
    ),
    (
        "ck_accounts_lane",
        {"primary_lane": ("data_ai", "text")},
        {"primary_lane": ("Data and AI", "text")},
    ),
]


@pytest.mark.parametrize(("name", "good", "bad"), CHECKS, ids=[c[0] for c in CHECKS])
def test_each_check_constraint(
    engine: Engine,
    name: str,
    good: dict[str, tuple[Any, str]],
    bad: dict[str, tuple[Any, str]],
) -> None:
    with engine.connect() as conn:
        assert constraint_holds(conn, name, good), f"{name} rejected a valid row"
        assert not constraint_holds(conn, name, bad), f"{name} accepted an invalid row"


def test_every_check_constraint_is_tested(engine: Engine) -> None:
    with engine.connect() as conn:
        assert check_constraint_names(conn, "intel.accounts") == {c[0] for c in CHECKS}


@pytest.mark.parametrize(
    "values",
    [
        {"domain": "Acme.com"},
        {"domain": "https://acme.com"},
        {"domain": "acme.com/careers"},
        {"domain": "www.acme.com"},
        {"domain": "acme"},
        {"name": " "},
        {"careers_url": "acme.com/jobs", "careers_url_verified_at": NOW},
        {"careers_url": "https://acme.com/jobs"},
        {"careers_url_verified_at": NOW},
        {"pursuing_client": " "},
        {"primary_lane": "Salesforce"},
    ],
)
def test_bad_rows_are_rejected_by_the_database(engine: Engine, values: dict[str, Any]) -> None:
    assert isinstance(_violation(engine, **values), CheckViolation)


def test_a_full_valid_row_is_accepted(engine: Engine) -> None:
    with engine.begin() as conn:
        _insert(
            conn,
            careers_url="https://careers.acme.com",
            careers_url_verified_at=NOW,
            industry="Manufacturing",
            primary_lane="salesforce",
            pursuing_client="Client Co",
        )


def test_index_on_tenant_and_name(engine: Engine) -> None:
    with engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'intel' "
                "AND indexname = 'ix_accounts_tenant_id_name'"
            )
        ).scalar_one()
    assert "(tenant_id, name)" in definition
