"""Document test fixtures: the connector harness (the hermetic stack builds on it) and an open
task endpoint for the local worker routes."""

import pytest

from tests.connectors.conftest import harness  # noqa: F401 - pytest fixture re-export


@pytest.fixture(autouse=True)
def _tasks_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
