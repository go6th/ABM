"""Research file test fixtures: the connector harness (the API test builds the hermetic stack on
it) and an open task endpoint for the local worker route."""

import pytest

from tests.connectors.conftest import harness  # noqa: F401 - pytest fixture re-export


@pytest.fixture(autouse=True)
def _tasks_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")
