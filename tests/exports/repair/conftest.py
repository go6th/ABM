"""The repair tests run with the application's client-file rules installed, as the running
application has them (``replica.repair_guard``); one test proves the fallback on its own."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from account_intelligence.replica import repair_guard
from exports.repair import package as PK


@pytest.fixture(autouse=True)
def _live_client_rules() -> Iterator[None]:
    PK.install_guard(repair_guard.build())
    yield
    PK.install_guard(PK.FALLBACK_GUARD)
