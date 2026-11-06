"""System-test fixtures: reuse the connector harness (scripted providers, fake clock)."""

from collections.abc import Iterator

import pytest

from tests.connectors.conftest import harness  # noqa: F401 - pytest fixture re-export


@pytest.fixture
def pin_workflow_v1(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ``full`` runs on Workflow v1 for the tests that pin its behaviour. Since ADR-0135 a
    tenant with no ``abm_agents`` rule runs the ABM graph; these tests describe the deprecated v1
    path and go when it is removed."""
    monkeypatch.setattr("agents.production.wiring.abm_flag_on", lambda *_a: False)
    yield
