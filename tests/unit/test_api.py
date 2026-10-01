from src.api.main import app


def test_foundation_api_routes_are_registered() -> None:
    paths = set(app.openapi()["paths"])
    assert "/healthz" in paths
    assert "/v1/runs" in paths
    assert "/api/inngest" in paths
