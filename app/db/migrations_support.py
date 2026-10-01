"""Settings shared by the Alembic environment and the migration tests."""

from __future__ import annotations

from typing import Any

from app.db.base import MANAGED_SCHEMAS

# Own version table in ``public``, so it never collides with the legacy chain's alembic_version.
VERSION_TABLE = "intel_alembic_version"
VERSION_TABLE_SCHEMA = "public"


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    """Autogenerate only looks at our schemas; legacy ``public`` tables are invisible to it."""
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    if type_ == "table":
        return name != VERSION_TABLE
    return True
