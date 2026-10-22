"""Ratchet: raw SQL (``sqlalchemy.text``) in the API route modules.

import-linter cannot forbid one symbol, so this walks the AST of every module under
``account_intelligence/api/routes`` and counts the ``from sqlalchemy import text`` lines and the
``text(...)`` call sites. Each file's count may only fall: lower a baseline entry when a route
moves its SQL into a repository; never raise one, never add a file.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "src" / "account_intelligence" / "api" / "routes"

# measured 2026-10-22; ratchet: shrink, never grow
BASELINE: dict[str, int] = {
    "account_workspace.py": 5,
    "accounts.py": 7,
    "admin_console.py": 16,
    "ask.py": 5,
    "audit.py": 2,
    "auth.py": 18,
    "connectors.py": 2,
    "crm.py": 5,
    "documents.py": 3,
    "exports.py": 3,
    "file_runs.py": 5,
    "health.py": 3,
    "internal_view.py": 4,
    "maildrive.py": 4,
    "me.py": 4,
    "operator_admin.py": 2,
    "operator_view.py": 6,
    "ops.py": 3,
    "research.py": 6,
    "runs.py": 16,
    "schedules.py": 2,
    "signals.py": 6,
    "spend.py": 5,
    "sso.py": 2,
    "support.py": 2,
    "tasks.py": 7,
}


def text_sites(path: Path) -> int:
    """``from sqlalchemy import text`` lines plus ``text(...)`` calls in one module."""
    count = 0
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "sqlalchemy"
            and any(alias.name == "text" for alias in node.names)
        ) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
        ):
            count += 1
    return count


def test_raw_sql_in_routes_never_grows() -> None:
    measured = {p.name: text_sites(p) for p in sorted(ROUTES.glob("*.py"))}
    assert measured, "no route modules found"
    grown = {
        name: (count, BASELINE.get(name, 0))
        for name, count in measured.items()
        if count > BASELINE.get(name, 0)
    }
    assert not grown, (
        "raw SQL grew in the API routes (file: now, baseline); move the query into a repository"
        f" instead of raising the baseline: {grown}"
    )


def test_the_baseline_names_only_existing_routes() -> None:
    """A deleted or renamed route must leave the baseline, so it cannot hide a new file's SQL."""
    missing = sorted(set(BASELINE) - {p.name for p in ROUTES.glob("*.py")})
    assert not missing, f"baseline entries without a route module: {missing}"
