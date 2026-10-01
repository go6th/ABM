"""Declarative base, schema names, constraint naming and shared mixins for data layer v1.

Two Postgres schemas (docs/data_layer/PLAN.md section 2): ``intel`` holds what deliverables are
built from; ``intel_internal`` holds raw object records and spend, and is never granted to a
client-facing role. Neither touches the legacy tables in ``public``.

Conventions every table follows are in docs/data_layer/CONVENTIONS.md and are checked by
``convention_violations``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped

from app.db.types import CreatedAt, JSONObject, TZDateTime, UpdatedAt, UUIDPk

SCHEMA = "intel"
INTERNAL_SCHEMA = "intel_internal"
MANAGED_SCHEMAS = (SCHEMA, INTERNAL_SCHEMA)

# Deterministic constraint names, so migrations can always drop or alter them by name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Python annotation -> column type, so models write ``Mapped[datetime]`` and get timestamptz.
TYPE_ANNOTATION_MAP: dict[Any, Any] = {
    uuid.UUID: Uuid(as_uuid=True),
    datetime: TZDateTime,
    dict[str, Any]: JSONObject(),
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = TYPE_ANNOTATION_MAP


class IdMixin:
    """UUID primary key named ``id``: uuid7 where the runtime has it, otherwise uuid4."""

    id: Mapped[UUIDPk]


class TimestampMixin:
    """``created_at`` and ``updated_at``, timezone-aware, defaulting to the server's now().

    ``updated_at`` is refreshed by SQLAlchemy (``onupdate``), not by a trigger. Updates made
    outside SQLAlchemy do not touch it; see CONVENTIONS.md, "updated_at limitations".
    """

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class Model(IdMixin, TimestampMixin, Base):
    """Abstract parent for every data layer table."""

    __abstract__ = True


def convention_violations(metadata: MetaData) -> list[str]:
    """Every table: a single UUID primary key ``id`` with a default, and both timestamps."""
    problems: list[str] = []
    for table in metadata.sorted_tables:
        name = table.fullname
        pk = list(table.primary_key.columns)
        if [c.name for c in pk] != ["id"]:
            problems.append(f"{name}: primary key must be the single column 'id'")
        elif not isinstance(pk[0].type, Uuid) or pk[0].default is None:
            problems.append(f"{name}: id must be a UUID with a default")
        if table.schema not in MANAGED_SCHEMAS:
            problems.append(f"{name}: schema must be one of {MANAGED_SCHEMAS}")
        for col_name in ("created_at", "updated_at"):
            col = table.columns.get(col_name)
            if col is None:
                problems.append(f"{name}: missing {col_name}")
                continue
            if not getattr(col.type, "timezone", False):
                problems.append(f"{name}: {col_name} must be timezone-aware")
            if col.nullable or col.server_default is None:
                problems.append(f"{name}: {col_name} must be NOT NULL with a server default")
        updated = table.columns.get("updated_at")
        if updated is not None and updated.onupdate is None:
            problems.append(f"{name}: updated_at must refresh on update (onupdate)")
    return problems
