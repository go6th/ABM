"""Tests for the shared conventions: UUID pks, timestamps, JSONB, naming rules."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable

from account_intelligence.db.base import NAMING_CONVENTION, Base, TimestampMixin, UuidPkMixin
from account_intelligence.db.types import JSONB as JSONB_TYPE


class _ScratchBase(DeclarativeBase):
    """Separate metadata so test models never enter the Alembic registry."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ScratchRow(UuidPkMixin, TimestampMixin, _ScratchBase):
    __tablename__ = "scratch_conventions"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)


def test_naming_convention_generates_deterministic_names() -> None:
    ddl = str(CreateTable(ScratchRow.__table__).compile(dialect=postgresql.dialect()))
    assert "CONSTRAINT pk_scratch_conventions PRIMARY KEY" in ddl
    assert "created_at" in ddl and "updated_at" in ddl


def test_jsonb_type_is_postgres_jsonb() -> None:
    column = ScratchRow.__table__.c.payload
    assert isinstance(column.type, JSONB_TYPE)
    assert column.type is JSONB or type(column.type) is JSONB


# Starter tables mapped for foreign keys only; they predate the v1 conventions.
STARTER_TABLES = {"tenants"}


def test_uuid_pk_and_timestamps_on_every_registered_model() -> None:
    """Every v1 model has a single-column UUID pk and both timestamps."""
    for table in Base.metadata.tables.values():
        (pk,) = table.primary_key.columns  # single-column UUID key (id, or account_id for counters)
        assert pk.type.python_type is UUID, table.name
        if table.name in STARTER_TABLES:
            continue
        for stamp in ("created_at", "updated_at"):
            assert stamp in table.c, table.name


def test_server_defaults_and_onupdate_roundtrip(sync_engine, admin) -> None:  # type: ignore[no-untyped-def]
    """Insert without id gets a server UUID; ORM update bumps updated_at; raw SQL does not.

    This is the documented trigger-free limitation: updated_at moves only for
    statements issued through SQLAlchemy (see docs/data_layer/CONVENTIONS.md).
    DDL runs as admin: app_rw holds no CREATE on the public schema by design.
    """
    engine = sync_engine
    admin.execute(str(CreateTable(ScratchRow.__table__).compile(dialect=postgresql.dialect())))
    admin.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON scratch_conventions TO app_rw")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                ScratchRow.__table__.insert().values(name="a", payload=["x", "y"])
            )
            row_id = result.inserted_primary_key[0]
        assert isinstance(row_id, UUID)

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT created_at, updated_at, payload FROM scratch_conventions WHERE id = :i"
                ),
                {"i": row_id},
            ).one()
        assert row.created_at.tzinfo is not None
        assert row.updated_at.tzinfo is not None
        assert row.payload == ["x", "y"]

        time.sleep(0.02)  # now() has microsecond resolution; make the bump visible
        with Session(engine) as session, session.begin():
            orm_row = session.get(ScratchRow, row_id)
            assert orm_row is not None
            orm_row.name = "b"

        with engine.begin() as conn:
            after = conn.execute(
                text("SELECT created_at, updated_at FROM scratch_conventions WHERE id = :i"),
                {"i": row_id},
            ).one()
        assert after.updated_at > row.created_at
        assert after.updated_at > row.updated_at

        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE scratch_conventions SET name = 'raw' WHERE id = %s", (str(row_id),)
            )
        with engine.begin() as conn:
            after_raw = conn.execute(
                text("SELECT updated_at FROM scratch_conventions WHERE id = :i"),
                {"i": row_id},
            ).one()
        assert after_raw.updated_at == after.updated_at
    finally:
        admin.execute("DROP TABLE IF EXISTS scratch_conventions")


def test_create_index_name_follows_convention() -> None:
    from sqlalchemy import Index

    ix = Index(None, ScratchRow.name)
    assert str(CreateIndex(ix).compile(dialect=postgresql.dialect())) == (
        "CREATE INDEX ix_scratch_conventions_name ON scratch_conventions (name)"
    )


def test_multi_column_constraint_names_join_columns_with_underscores() -> None:
    """Rendered names must match the names the SQL migrations create (alembic check)."""
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.schema import ForeignKeyConstraint

    from account_intelligence.db.models import Account, Run

    uniques = {c.name for c in Account.__table__.constraints if isinstance(c, UniqueConstraint)}
    assert {"uq_accounts_tenant_id_domain", "uq_accounts_tenant_id_id"} <= uniques
    fks = {c.name for c in Run.__table__.constraints if isinstance(c, ForeignKeyConstraint)}
    assert "fk_runs_tenant_id_account_id_accounts" in fks
