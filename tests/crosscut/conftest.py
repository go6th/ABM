"""Cross-cutting fixtures (reuse the Phase 3 and Phase 4 helpers)."""

from __future__ import annotations

from tests.phase3.conftest import browser, plane  # noqa: F401  (signed-in API fixtures)
from tests.phase4.conftest import NOW, db, fact, tenant_with_account  # noqa: F401
