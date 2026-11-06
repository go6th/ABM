"""Allow-list: which API modules open an untenanted session or switch to the system scope.

``untenanted_session`` and ``set_system`` bypass the tenant row-level security, so the set of
callers inside ``account_intelligence.api`` is frozen. A new caller must be reviewed and added
here on purpose; a caller that goes away is removed so the list never carries dead entries.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
API = SRC / "account_intelligence" / "api"
CALLS = frozenset({"untenanted_session", "set_system"})

# measured 2026-10-22
ALLOWED: frozenset[str] = frozenset(
    {
        "account_intelligence.api.auth",
        "account_intelligence.api.main",
        "account_intelligence.api.operator",
        "account_intelligence.api.routes.admin_console",
        "account_intelligence.api.routes.auth",
        "account_intelligence.api.routes.feedback",
        "account_intelligence.api.routes.library",
        "account_intelligence.api.routes.operator_admin",
        "account_intelligence.api.routes.sso",
        "account_intelligence.api.routes.tasks",
        # reviewed 2026-10-23: only the local_auth_* SECURITY DEFINER functions (look up, create
        # and invite an identity), never a tenant table
        "account_intelligence.api.team_invites",
    }
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _callers() -> set[str]:
    found: set[str] = set()
    for path in sorted(API.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in CALLS:
                found.add(_module_name(path))
    return found


def test_only_the_allow_listed_api_modules_bypass_tenant_scope() -> None:
    callers = _callers()
    assert callers == set(ALLOWED), (
        f"new callers (review before allowing): {sorted(callers - ALLOWED)};"
        f" stale allow-list entries (remove): {sorted(ALLOWED - callers)}"
    )


def test_the_tenant_session_module_does_not_carry_the_way_out() -> None:
    """ADR-0123: the system scope and the untenanted session live in ``db.system_scope`` alone,
    which import-linter lets only the allow-listed modules import; a re-export from
    ``db.session`` (imported everywhere) would bypass that contract."""
    import account_intelligence.db.session as session
    import account_intelligence.db.system_scope as scope

    for name in ("untenanted_session", "set_system", "system_scope"):
        assert not hasattr(session, name), name
        assert callable(getattr(scope, name)), name
