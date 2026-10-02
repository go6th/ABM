"""Declarative base and schema-object naming conventions.

Every data layer model inherits from Base. The naming convention gives Alembic
deterministic names for indexes and constraints so migrations are stable and
reversible (Alembic cookbook: using ALTER with named constraints).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every data layer model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
