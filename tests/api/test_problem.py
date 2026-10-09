"""Synthetic checks for the public problem-details envelope."""

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from account_intelligence.api.pagination import InvalidCursor, decode_cursor, encode_cursor
from account_intelligence.api.problem import problem_body


def test_problem_details_hide_internal_text() -> None:
    """GUARANTEE(test_problem_details_hide_internal_text): errors never echo supplied text."""
    result = problem_body(500, "req-1", "password=secret database broke")
    assert result == {
        "type": "urn:6th:problem:500",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "Internal Server Error",
        "request_id": "req-1",
    }


def test_known_auth_detail_is_safe() -> None:
    """A small fixed authentication message remains useful to callers."""
    assert problem_body(401, "req-2", "invalid token")["detail"] == "invalid token"


def test_signed_cursor_is_tenant_bound() -> None:
    """GUARANTEE(test_signed_cursor_is_tenant_bound): signed cursors cannot cross scope."""
    cursor = encode_cursor(
        tenant_id="tenant-a", collection="runs", position=["2026-01-01", "run-1"], secret=b"s" * 32
    )
    assert decode_cursor(cursor, tenant_id="tenant-a", collection="runs", secret=b"s" * 32) == [
        "2026-01-01",
        "run-1",
    ]
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor, tenant_id="tenant-b", collection="runs", secret=b"s" * 32)
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor[:-1] + "x", tenant_id="tenant-a", collection="runs", secret=b"s" * 32)


@pytest.mark.asyncio
async def test_validation_problem_does_not_echo_submitted_values(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Invalid input gets a safe 422 with request id and baseline security headers."""
    import httpx

    from account_intelligence.api import main

    app = main.create_app()
    monkeypatch.setattr(main, "_observe", lambda *_args: None)

    class Payload(BaseModel):
        count: int

    @app.post("/test-validation")
    async def validate(payload: Payload) -> dict[str, int]:
        return {"count": payload.count}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/test-validation", json={"count": "private-value"})

    assert response.status_code == 422
    assert "private-value" not in response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["errors"][0]["loc"] == ["body", "count"]


@pytest.mark.asyncio
async def test_framework_http_problem_uses_stable_envelope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Framework errors retain safe headers without exposing arbitrary detail objects."""
    import httpx

    from account_intelligence.api import main

    app = main.create_app()
    monkeypatch.setattr(main, "_observe", lambda *_args: None)

    @app.get("/test-http-error")
    async def fail() -> None:
        raise HTTPException(
            status_code=429, detail={"token": "private-value"}, headers={"Retry-After": "2"}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/test-http-error")

    assert response.status_code == 429
    assert "private-value" not in response.text
    assert response.headers["retry-after"] == "2"
    assert response.json()["detail"] == "Too Many Requests"
