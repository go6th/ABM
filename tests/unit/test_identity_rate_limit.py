"""Local first-party auth rate limits keep identifiers out of bucket names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from account_intelligence.api.errors import APIProblem
from identity import rate_limit


def _request(address: str = "203.0.113.8") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/v1/auth/login",
            "raw_path": b"/v1/auth/login",
            "query_string": b"",
            "headers": [],
            "client": (address, 50000),
            "server": ("app.test", 443),
        }
    )


@pytest.mark.asyncio
async def test_auth_rate_limit_is_per_ip_and_account_without_plaintext_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account bucket returns bounded retry time without storing its email key in memory."""
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(redis_host=None),
    )
    rate_limit._memory.clear()
    for _ in range(10):
        await rate_limit.consume(_request(), account="Person@Example.test", action="login")
    with pytest.raises(APIProblem) as error:
        await rate_limit.consume(_request(), account="person@example.test", action="login")
    assert error.value.status == 429
    assert error.value.headers["Retry-After"] == "10"
    assert all(
        "person@example.test" not in key
        for bucket in rate_limit._memory.values()
        for key in bucket._buckets
    )
