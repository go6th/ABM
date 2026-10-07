"""Plan regression (Phase 3 harness item 13): the client table query uses the tenant-leading index
under RLS, never a sequential scan of ``view_rows``. SYNTHETIC rows; no latency asserted.

GUARANTEE target: ``views.query.compile_query`` with ``ix_view_rows_table``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.conftest import ADMIN_DSN, APP_DSN

pytestmark = pytest.mark.requires_postgres
ROOT = Path(__file__).resolve().parents[2]


def _smoke() -> object:
    spec = importlib.util.spec_from_file_location("scale_smoke", ROOT / "scripts/scale_smoke.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_table_query_uses_the_tenant_index_under_rls() -> None:
    smoke = _smoke()
    seeded = smoke.seed(ADMIN_DSN, 10, 3, 1000)  # type: ignore[attr-defined]
    for tenant, run in (seeded[0], seeded[-1]):
        explain = smoke.plan(APP_DSN, tenant, run)  # type: ignore[attr-defined]
        assert "ix_view_rows_table" in explain, explain
        assert "Seq Scan on view_rows" not in explain, explain
