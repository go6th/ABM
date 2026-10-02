"""Declarative base, shared mixins and schema-object naming conventions.

Every data layer model inherits from Base. The naming convention gives Alembic
deterministic names for indexes and constraints so migrations are stable and
reversible (Alembic cookbook: using ALTER with named constraints).

Conventions (see docs/data_layer/CONVENTIONS.md):

- Primary keys are UUID columns with a server-side gen_random_uuid() default, so
  raw SQL inserts (fixtures, migrations, psql sessions) get ids for free. uuid7
  is not available on the supported Python range (3.11 to 3.12); the swap path
  is documented in CONVENTIONS.md.
- created_at and updated_at are timezone-aware with server defaults now().
  updated_at also carries SQLAlchemy onupdate=func.now(), which is trigger-free
  by design: it fires for ORM and Core UPDATE statements issued through
  SQLAlchemy, and silently does nothing for raw SQL updates (known limitation,
  documented and accepted for v1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import MetaData, Uuid, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every data layer model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every bare ``Mapped[datetime]`` is timestamptz, matching the migrations.
    type_annotation_map = {datetime: DateTime(timezone=True)}


class UuidPkMixin:
    """UUID primary key with a server-side default (works for raw SQL inserts)."""

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """created_at/updated_at, timezone-aware, server defaults, ORM-side onupdate."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
