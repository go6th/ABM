"""The enhanced skill's tenancy check against the real migrated schema (decision R-01)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import psycopg

TENANCY = (
    Path(__file__).resolve().parents[3]
    / ".claude/skills/account-intelligence/scripts/check_tenancy.py"
)


def _run(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(TENANCY), "--url", url, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_reports_current_state_without_failing(migrated_database_url: str) -> None:
    result = _run(migrated_database_url)
    assert result.returncode == 0
    assert "intel.accounts: row level security not enabled and forced" in result.stdout
    assert "intel.runs: no tenant_id column" in result.stdout


def test_strict_mode_fails_until_r01_is_implemented(migrated_database_url: str) -> None:
    result = _run(migrated_database_url, "--strict")
    assert result.returncode == 1
    assert "FAILED" in result.stdout


def test_strict_mode_passes_on_a_tenant_isolated_table(empty_database_url: str) -> None:
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA intel")
        conn.execute("CREATE TABLE intel.t (id int PRIMARY KEY, tenant_id uuid NOT NULL)")
        conn.execute("ALTER TABLE intel.t ENABLE ROW LEVEL SECURITY")
        conn.execute("ALTER TABLE intel.t FORCE ROW LEVEL SECURITY")
        conn.execute(
            "CREATE POLICY tenant_isolation ON intel.t USING (tenant_id IS NOT NULL) "
            "WITH CHECK (tenant_id IS NOT NULL)"
        )
    result = _run(empty_database_url, "--strict")
    assert result.returncode == 0, result.stdout
    assert "intel.t: ok" in result.stdout


def test_rls_without_policy_is_reported(empty_database_url: str) -> None:
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA intel")
        conn.execute("CREATE TABLE intel.t (id int PRIMARY KEY, tenant_id uuid NOT NULL)")
        conn.execute("ALTER TABLE intel.t ENABLE ROW LEVEL SECURITY")
        conn.execute("ALTER TABLE intel.t FORCE ROW LEVEL SECURITY")
    assert "no policy" in _run(empty_database_url).stdout
