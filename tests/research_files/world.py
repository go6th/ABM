"""Shared helper for the database tests that reuse the research file parsers (documents)."""

from __future__ import annotations

from typing import Any

import psycopg


def admin(sql: str, *params: object) -> list[tuple[Any, ...]]:
    """Run one statement as the scratch database's admin and return its rows."""
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return list(cur.fetchall()) if cur.description else []
