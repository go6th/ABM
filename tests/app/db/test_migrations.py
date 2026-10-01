"""Alembic setup for data layer v1: upgrade and downgrade on an empty database, drift check."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine

from app.db.base import MANAGED_SCHEMAS, NAMING_CONVENTION, Base
from app.db.url import DatabaseUrlMissing, sqlalchemy_url

REPO = Path(__file__).resolve().parents[3]
INI = REPO / "app" / "db" / "alembic.ini"
VERSION_TABLE = "intel_alembic_version"


def _config() -> Config:
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture()
def db(empty_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    yield empty_database_url


def _schemas(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT nspname FROM pg_namespace").fetchall()
    return {r[0] for r in rows}


def _version(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        exists = conn.execute("SELECT to_regclass(%s)", (f"public.{VERSION_TABLE}",)).fetchone()
        if not exists or exists[0] is None:
            return []
        query = sql.SQL("SELECT version_num FROM public.{}").format(sql.Identifier(VERSION_TABLE))
        return [r[0] for r in conn.execute(query)]


# ------------------------------------------------------------------ no database needed


def test_single_linear_history() -> None:
    script = ScriptDirectory.from_config(_config())
    heads = script.get_heads()
    assert len(heads) == 1
    for rev in script.walk_revisions():
        assert not isinstance(rev.down_revision, tuple | list), f"merge revision {rev.revision}"


def test_offline_sql_renders_without_a_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    command.upgrade(_config(), "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE SCHEMA intel;" in out
    assert f"CREATE TABLE public.{VERSION_TABLE}" in out


def test_missing_database_url_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseUrlMissing, match="DATABASE_URL is not set"):
        command.upgrade(_config(), "head")


def test_url_uses_psycopg3_driver() -> None:
    assert sqlalchemy_url("postgresql://u:p@h/db").drivername == "postgresql+psycopg"
    assert sqlalchemy_url("postgresql+psycopg://u@h/db").drivername == "postgresql+psycopg"
    with pytest.raises(ValueError, match="postgresql"):
        sqlalchemy_url("sqlite:///x.db")


def test_naming_convention_is_installed() -> None:
    assert Base.metadata.naming_convention == {
        **Base.metadata.naming_convention,
        **NAMING_CONVENTION,
    }
    for key in ("pk", "fk", "uq", "ck", "ix"):
        assert key in Base.metadata.naming_convention


# ------------------------------------------------------------------ live empty database


def test_upgrade_head_on_empty_database(db: str) -> None:
    assert not set(MANAGED_SCHEMAS) & _schemas(db)
    command.upgrade(_config(), "head")
    assert set(MANAGED_SCHEMAS) <= _schemas(db)
    head = ScriptDirectory.from_config(_config()).get_current_head()
    assert _version(db) == [head]


def test_downgrade_base_returns_to_empty(db: str) -> None:
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    assert not set(MANAGED_SCHEMAS) & _schemas(db)
    assert _version(db) == []


def test_round_trip_is_repeatable(db: str) -> None:
    for _ in range(2):
        command.upgrade(_config(), "head")
        command.downgrade(_config(), "base")
    command.upgrade(_config(), "head")
    assert set(MANAGED_SCHEMAS) <= _schemas(db)


def test_legacy_alembic_chain_is_not_touched(db: str) -> None:
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)")
        conn.execute("INSERT INTO public.alembic_version VALUES ('0001_initial')")
        conn.execute("CREATE TABLE public.accounts (id int PRIMARY KEY)")
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT version_num FROM public.alembic_version").fetchall() == [
            ("0001_initial",)
        ]
        assert conn.execute("SELECT to_regclass('public.accounts')").fetchone() == ("accounts",)


def test_models_and_migrations_do_not_drift(db: str) -> None:
    """After upgrade head, autogenerate must find nothing to change (and ignore legacy tables)."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("CREATE TABLE public.facts (id int PRIMARY KEY)")
    command.upgrade(_config(), "head")
    from app.db.migrations_support import include_name

    engine = create_engine(sqlalchemy_url(db))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={
                    "include_schemas": True,
                    "include_name": include_name,
                    "compare_type": True,
                    "compare_server_default": True,
                    "target_metadata": Base.metadata,
                },
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_drift_check_would_catch_an_unmigrated_table(db: str) -> None:
    """Guards the guard: a model with no migration must show up as a difference."""
    from sqlalchemy import Column, Integer, MetaData, Table

    from app.db.migrations_support import include_name

    command.upgrade(_config(), "head")
    probe = MetaData(naming_convention=NAMING_CONVENTION)
    Table("probe", probe, Column("id", Integer, primary_key=True), schema="intel")
    engine = create_engine(sqlalchemy_url(db))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"include_schemas": True, "include_name": include_name}
            )
            diff = compare_metadata(ctx, probe)
    finally:
        engine.dispose()
    added = [d[1].fullname for d in diff if d[0] == "add_table"]
    assert added == ["intel.probe"]


def test_enum_checks_in_database_match_code(db: str) -> None:
    """Every enum column's CHECK after upgrade head allows exactly the enum's current values."""
    from app.db.enums import enum_check_drift

    command.upgrade(_config(), "head")
    engine = create_engine(sqlalchemy_url(db))
    try:
        with engine.connect() as conn:
            assert enum_check_drift(conn, Base.metadata) == []
    finally:
        engine.dispose()
