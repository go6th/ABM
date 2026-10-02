"""Shared column types for the data layer.

Postgres-native types are re-exported here so models do not import from
sqlalchemy.dialects directly and conventions stay in one place.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB

__all__ = ["JSONB"]

# JSONB: binary JSON with GIN-indexable containment. Used for proof query
# lists, certifications, prior employers and similar list-shaped payloads.
