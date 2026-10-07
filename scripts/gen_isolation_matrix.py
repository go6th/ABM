"""Isolation matrix coverage (TN1): every tenant object discovered, every one covered.

Discovers, then compares with ``tests/isolation/matrix/matrix.yaml``:

* tables: every public table (not a partition child) with a ``tenant_id`` or ``thread_id`` column,
  from the migrated database's catalog;
* views: every public view, from the catalog;
* functions: every ``SECURITY DEFINER`` function in ``public`` (they run with the owner's rights,
  so each must be narrow and return identifiers only);
* routes: every ``/v1``, ``/operator`` and ``/tasks`` route of the application;
* storage layouts, cache purposes, event types and jobs: ``tenancy.catalog``.

An object with no matrix entry is UNCOVERED; an entry with no object is STALE. Either fails
``--check`` (exit 1), so a new table, route or layout cannot ship without its cross-tenant
probes. ``--skeleton`` prints YAML entries for what is uncovered.

Usage: ``python scripts/gen_isolation_matrix.py --dsn postgresql://... [--check|--skeleton]``
(``--dsn`` defaults to ``$ADMIN_DSN``).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests/isolation/matrix/matrix.yaml"
SECTIONS = ("tables", "views", "functions", "routes", "storage", "cache", "events", "jobs")
_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://app_rw:unused@localhost:5432/unused",
    "CHECKPOINT_DATABASE_URL": "postgresql://app_rw:unused@localhost:5432/unused",
    "JWKS_URL": "http://unused.invalid/jwks",
    "JWT_AUDIENCE": "unused",
    "JWT_ISSUER": "unused",
}


@dataclass
class Report:
    """Uncovered and stale objects per section."""

    uncovered: dict[str, list[str]] = field(default_factory=dict)
    stale: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when every section is fully and exactly covered."""
        return not any(self.uncovered.values()) and not any(self.stale.values())


def discover_db(dsn: str) -> dict[str, set[str]]:
    """Tenant tables, public views and definer functions from the database catalog."""
    import psycopg

    with psycopg.connect(dsn) as c:
        tables = {
            r[0]
            for r in c.execute(
                """
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
                   AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                                AND a.attname IN ('tenant_id', 'thread_id') AND NOT a.attisdropped)
                """
            ).fetchall()
        }
        views = {
            r[0]
            for r in c.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')"
            ).fetchall()
        }
        functions = {
            r[0]
            for r in c.execute(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = 'public' AND p.prosecdef"
            ).fetchall()
        }
    return {"tables": tables, "views": views, "functions": functions}


def discover_routes() -> set[str]:
    """``METHOD /path`` for every tenant, operator and worker route of the application."""
    for k, v in _ENV_DEFAULTS.items():
        os.environ.setdefault(k, v)
    from account_intelligence.api.main import create_app

    out: set[str] = set()
    for path, ops in create_app().openapi()["paths"].items():
        if not path.startswith(("/v1/", "/operator/", "/tasks/")):
            continue
        for method in ops:
            if method not in ("parameters", "head"):
                out.add(f"{method.upper()} {path}")
    return out


def discover_registries() -> dict[str, set[str]]:
    """Storage layouts, cache purposes, event types and jobs from ``tenancy.catalog``."""
    from tenancy import catalog

    return {
        "storage": set(catalog.STORAGE_LAYOUTS),
        "cache": set(catalog.CACHE_PURPOSES),
        "events": set(catalog.EVENT_TYPES),
        "jobs": set(catalog.JOBS),
    }


def load_matrix(path: Path = MATRIX) -> dict[str, Any]:
    """The matrix file as a mapping of section to entries."""
    data = yaml.safe_load(path.read_text()) or {}
    return {s: dict(data.get(s) or {}) for s in SECTIONS}


def compare(discovered: dict[str, set[str]], matrix: dict[str, Any]) -> Report:
    """Uncovered (discovered, no entry) and stale (entry, nothing discovered) per section."""
    report = Report()
    for section in SECTIONS:
        found = discovered.get(section, set())
        entries = set(matrix.get(section, {}))
        report.uncovered[section] = sorted(found - entries)
        report.stale[section] = sorted(entries - found)
    return report


def discover(dsn: str) -> dict[str, set[str]]:
    """Every discoverable tenant object."""
    return {**discover_db(dsn), "routes": discover_routes(), **discover_registries()}


def main(argv: list[str] | None = None) -> int:
    """Print the coverage report; exit 1 under ``--check`` when anything is uncovered or stale."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dsn", default=os.environ.get("ADMIN_DSN"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skeleton", action="store_true")
    args = parser.parse_args(argv)
    if not args.dsn:
        print("--dsn or ADMIN_DSN is required (a migrated database)", file=sys.stderr)
        return 2
    report = compare(discover(args.dsn), load_matrix())
    for section in SECTIONS:
        for name in report.uncovered[section]:
            print(f"UNCOVERED {section}: {name}")
            if args.skeleton:
                print(f"  {name!r}: {{}}")
        for name in report.stale[section]:
            print(f"STALE {section}: {name}")
    if report.ok:
        print("isolation matrix: every discovered object is covered")
    return 1 if (args.check and not report.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
