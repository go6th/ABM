"""HTTP client factory and a single bounded exchange.

Explicit timeouts, connection limits, TLS verification always on, egress through the
configured proxy, no redirects followed (provider APIs do not redirect; a redirect is
an error), body size capped while streaming, content type checked. Failures are
classified into the typed hierarchy; the connector never retries here.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from account_intelligence.connectors.base import PageRequest, RawResponse
from account_intelligence.connectors.errors import (
    NonRetryableProviderError,
    ResponseTooLarge,
    RetryableProviderError,
    SecurityError,
)


@dataclass(frozen=True)
class HttpSettings:
    """One provider's HTTP limits: base URL, timeouts, pool size, body cap, content type."""

    base_url: str
    connect_s: float = 5.0
    read_s: float = 30.0
    write_s: float = 10.0
    pool_s: float = 5.0
    total_s: float = 60.0
    max_connections: int = 10
    max_keepalive: int = 5
    max_bytes: int = 10_000_000
    content_types: tuple[str, ...] = ("application/json",)


def make_client(
    settings: HttpSettings,
    *,
    headers: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
    env: Mapping[str, str] | None = None,
) -> httpx.AsyncClient:
    """An httpx client with TLS verification on, no redirects, explicit timeouts and the proxy."""
    if not settings.base_url.startswith("https://"):
        raise SecurityError("provider base URL must be https")
    source = env if env is not None else os.environ
    proxy = source.get("CONNECTOR_EGRESS_PROXY") or None
    return httpx.AsyncClient(
        base_url=settings.base_url,
        headers=dict(headers),
        timeout=httpx.Timeout(
            connect=settings.connect_s,
            read=settings.read_s,
            write=settings.write_s,
            pool=settings.pool_s,
        ),
        limits=httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive,
        ),
        verify=True,
        follow_redirects=False,
        trust_env=False,  # proxy only from CONNECTOR_EGRESS_PROXY, never ambient settings
        proxy=None if transport is not None else proxy,
        transport=transport,
    )


async def exchange(
    client: httpx.AsyncClient,
    request: PageRequest,
    settings: HttpSettings,
    *,
    deadline: float | None = None,
    monotonic: object = time.monotonic,
) -> RawResponse:
    """One request with a total deadline and a streamed size cap. Never retries."""
    import asyncio

    clock = monotonic if callable(monotonic) else time.monotonic
    started = clock()
    budget = settings.total_s
    if deadline is not None:
        budget = min(budget, deadline - started)
    if budget <= 0:
        raise RetryableProviderError("slice deadline reached before the call")
    try:
        async with asyncio.timeout(budget):
            async with client.stream(
                request.method,
                request.path,
                params=dict(request.params) if request.params else None,
                json=dict(request.json_body) if request.json_body is not None else None,
            ) as response:
                declared = response.headers.get("content-length")
                if (
                    declared is not None
                    and declared.isdigit()
                    and int(declared) > settings.max_bytes
                ):
                    raise ResponseTooLarge(
                        "declared length over cap", http_status=response.status_code
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():  # decoded (gzip handled)
                    size += len(chunk)
                    if size > settings.max_bytes:
                        raise ResponseTooLarge("body over cap", http_status=response.status_code)
                    chunks.append(chunk)
                body = b"".join(chunks)
                status = response.status_code
                headers = {k.lower(): v for k, v in response.headers.items()}
    except TimeoutError as error:
        raise RetryableProviderError("total deadline exceeded") from error
    except httpx.TimeoutException as error:
        raise RetryableProviderError(f"timeout: {type(error).__name__}") from error
    except (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ) as error:
        # includes connection reset and a body truncated against its content-length
        raise RetryableProviderError(f"transport: {type(error).__name__}") from error
    except httpx.DecodingError as error:
        raise RetryableProviderError("body could not be decoded") from error
    except httpx.TooManyRedirects as error:  # pragma: no cover - follow_redirects is off
        raise NonRetryableProviderError("redirect") from error
    latency_ms = int((clock() - started) * 1000)
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return RawResponse(
        status_code=status,
        content_type=content_type,
        body=body,
        headers={k: v for k, v in headers.items() if k in _KEPT_HEADERS},
        latency_ms=latency_ms,
        request_path=request.path,
    )


# Only these response headers are kept on the raw response (never cookies or auth).
_KEPT_HEADERS = frozenset(
    {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "content-length"}
)
