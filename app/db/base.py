"""Declarative base, schema names and the constraint naming convention for data layer v1.

Two Postgres schemas (docs/data_layer/PLAN.md section 2): ``intel`` holds what deliverables are
built from; ``intel_internal`` holds raw object records and spend, and is never granted to a
client-facing role. Neither touches the legacy tables in ``public``.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
