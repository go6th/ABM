"""The API exposes the run, task and probe routes (and no Inngest mount)."""

from account_intelligence.api.main import app


def test_foundation_api_routes_are_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/healthz" in paths
    assert "/v1/runs" in paths
    # Inngest is retired (Google Cloud override, conflict row C11): Cloud Tasks call the worker
    assert "/tasks/run-slice" in paths and "/tasks/approval-timeout" in paths
    assert "/readyz" in paths and "/api/inngest" not in paths
