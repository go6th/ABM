"""API hardening without a database: security headers on every response (HSTS over HTTPS), the
API's own request log carries no query string or token (security audit L10), and the cloud
profiles serve no interactive docs or schema route (L12)."""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from account_intelligence.api.main import create_app
from obs.catalog import load_catalog
from obs.exporters import DropCounter
from obs.logging import JsonFormatter

SECRET = "tok3nValueThatMustNeverBeLogged"


@pytest.fixture
def client() -> TestClient:
    # no lifespan: the routes answer from the guard (401) without a container or database
    return TestClient(create_app(), raise_server_exceptions=False)


def test_every_api_response_carries_the_security_headers(client: TestClient) -> None:
    for path in ("/healthz", "/v1/me", "/no-such-route"):
        reply = client.get(path)
        assert reply.headers["X-Content-Type-Options"] == "nosniff"
        assert reply.headers["X-Frame-Options"] == "DENY"
        assert reply.headers["Referrer-Policy"] == "no-referrer"
        assert reply.headers["Cache-Control"] == "no-store"
        assert reply.headers["Content-Security-Policy"] == (
            "default-src 'none'; frame-ancestors 'none'"
        )
        assert "Strict-Transport-Security" not in reply.headers  # plain HTTP: no HSTS
    secure = client.get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert secure.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_the_request_log_has_the_route_template_and_never_the_query(client: TestClient) -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter(load_catalog(), DropCounter(), "api"))
    log = logging.getLogger("account_intelligence.api.main")
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    try:
        client.get(f"/v1/downloads/job-1/parts/2?token={SECRET}")
        client.put(f"/v1/documents/doc-1/content?token={SECRET}", content=b"x")
        client.get("/healthz?token=" + SECRET)
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)
    text = buf.getvalue()
    assert SECRET not in text and "token" not in text and "job-1" not in text
    lines = [json.loads(x) for x in text.splitlines()]
    requests = [x for x in lines if x["event"] == "http.request"]
    assert [(x["method"], x["route"]) for x in requests] == [
        ("GET", "/v1/downloads/{job_id}/parts/{n}"),
        ("PUT", "/v1/documents/{document_id}/content"),
    ]  # the probe is not logged
    assert all(
        isinstance(x["http_status"], int) and x["latency_ms"] >= 0 and x["request_id"]
        for x in requests
    )


@pytest.mark.parametrize("env", ["staging", "production"])
def test_the_cloud_profiles_serve_no_docs_or_schema(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    assert app.openapi()["paths"]  # the snapshot export still works


def test_the_local_profiles_keep_the_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    assert TestClient(create_app()).get("/openapi.json").status_code == 200
