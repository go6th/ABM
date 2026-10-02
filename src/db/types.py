"""Shared column types for the data layer.

Postgres-native types are re-exported here so models do not import from
sqlalchemy.dialects directly and conventions stay in one place.
"""

from __future__ import annotations

from sqlalchemy import Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import CHAR, TypeEngine

# JSONB: binary JSON with GIN-indexable containment. Used for proof query
# lists, certifications, prior employers and similar list-shaped payloads.
JSONB = JSONB

# SHA256_HEX: char(64) column type for hex digests (url_hash, request_hash,
# content_sha256). Fixed width keeps indexes tight and rejects short values.
SHA256_HEX = CHAR(64)

__all__ = ["JSONB", "SHA256_HEX", "Uuid", "TypeEngine"]
