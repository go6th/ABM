"""The nightly isolation check's results (platform rows in ``isolation_checks``; no tenant data).

``latest`` is what the admin console's pill and cards show: Passed or Failed with the time and
how many workspaces were checked, or Not run yet when no pass has been recorded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


async def latest(plain: Any) -> dict[str, Any]:
    """The most recent pass (read without tenant context: the table is platform-level)."""
    row = (
        await plain.execute(
            text(
                "SELECT ran_at, passed, workspaces_checked, failures FROM isolation_checks"
                " ORDER BY ran_at DESC LIMIT 1"
            )
        )
    ).first()
    if row is None:
        return {"status": "not_run", "ran_at": None, "workspaces_checked": 0, "failures": 0}
    return {
        "status": "passed" if row.passed else "failed",
        "ran_at": row.ran_at.isoformat(),
        "workspaces_checked": int(row.workspaces_checked),
        "failures": int(row.failures),
    }
