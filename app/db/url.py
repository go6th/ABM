"""Database URL handling: read from the environment only, and select the psycopg 3 driver."""

from __future__ import annotations

import os

from sqlalchemy.engine import URL, make_url

ENV_VAR = "DATABASE_URL"


class DatabaseUrlMissing(RuntimeError):
    pass


def sqlalchemy_url(raw: str) -> URL:
    """``postgresql://...`` defaults to psycopg2 in SQLAlchemy; this project uses psycopg 3."""
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"expected a postgresql URL, got {url.get_backend_name()!r}")
    if url.drivername in {"postgresql", "postgres"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


def database_url_from_env() -> URL:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        raise DatabaseUrlMissing(f"{ENV_VAR} is not set; export it (see .env.example)")
    return sqlalchemy_url(raw)
