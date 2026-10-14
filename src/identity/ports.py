"""The identity broker's outbound boundary (D-62): the tenant's OIDC provider.

``IdentityProvider`` exchanges an authorization code at the connection's token endpoint and
fetches the connection's signing keys. ``HttpIdentityProvider`` is the production adapter over
``httpx`` (standard OAuth 2.0 authorization-code exchange with PKCE, client secret from
``SecretStore``); it has been exercised only against the fake IdP in the tests, never a real
provider ([VERIFY] each IdP's token endpoint authentication method). SAML needs no outbound call:
the tenant admin enters the IdP's certificate and sign-in URL.

Tenant admins configure the IdP endpoints, so every outbound call is SSRF-guarded: HTTPS on port
443 only, and every address the host resolves to must be public (no loopback, private,
link-local, reserved or multicast address), with redirects off. The production client
(``pinned_client``) connects through ``PinnedBackend``, which resolves the host, checks every
address and opens the TCP connection to that same checked address in one step, so a DNS answer
that changes between the check and the connect (rebinding) is never used (security audit L17);
TLS still verifies the certificate for the host name (SNI unchanged).

No token, code or secret is logged or returned in an error message.

GUARANTEE(test_idp_calls_refuse_private_addresses_and_redirects)
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpcore
import httpx

Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def require_public_https(url: str, resolve: Resolver) -> None:
    """IdentityProviderError unless ``url`` is HTTPS and its host resolves only to public
    addresses."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise IdentityProviderError("idp_endpoint_refused")
    if parts.port not in (None, 443):
        raise IdentityProviderError("idp_endpoint_refused")
    await public_addresses(parts.hostname, resolve)


async def public_addresses(host: str, resolve: Resolver) -> list[str]:
    """Every address of ``host``, all of them public (IdentityProviderError otherwise)."""
    try:
        addresses = await resolve(host)
    except OSError:
        raise IdentityProviderError("idp_unreachable") from None
    if not addresses:
        raise IdentityProviderError("idp_unreachable")
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise IdentityProviderError("idp_endpoint_refused")
    return addresses


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """An ``httpcore`` network backend that resolves, checks and connects in one step: the TCP
    connection goes to an address that ``public_addresses`` has just approved, never to a second
    resolution (see the module docstring). Port 443 only."""

    def __init__(self, resolve: Resolver = system_resolver, inner: Any = None) -> None:
        from httpcore._backends.auto import AutoBackend

        self._resolve = resolve
        self._inner = inner or AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        """Connect to the first approved address of ``host``."""
        if port != 443:
            raise IdentityProviderError("idp_endpoint_refused")
        address = (await public_addresses(host, self._resolve))[0]
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
        """Never: the IdP is reached over TCP only."""
        raise IdentityProviderError("idp_endpoint_refused")

    async def sleep(self, seconds: float) -> None:
        """The inner backend's sleep."""
        await self._inner.sleep(seconds)


def pinned_client(resolve: Resolver = system_resolver) -> httpx.AsyncClient:
    """The production IdP client: no redirects, every connection through ``PinnedBackend``."""
    transport = httpx.AsyncHTTPTransport(retries=0)
    # httpcore takes the backend at construction only; httpx does not pass it through
    transport._pool._network_backend = PinnedBackend(resolve)
    return httpx.AsyncClient(transport=transport, follow_redirects=False)


class IdentityProviderError(RuntimeError):
    """The IdP refused or failed; carries a reason code, never the IdP's response body."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class OidcEndpoints:
    """What the broker needs from one OIDC connection to call its IdP."""

    issuer: str
    client_id: str
    token_endpoint: str
    jwks_uri: str


class IdentityProvider(Protocol):
    """The tenant's OIDC provider."""

    async def exchange_code(
        self,
        conn: OidcEndpoints,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_secret: str | None,
    ) -> str:
        """The ID token for an authorization code (IdentityProviderError otherwise)."""
        ...

    async def jwks(self, conn: OidcEndpoints) -> dict[str, Any]:
        """The connection's JSON Web Key Set."""
        ...


class HttpIdentityProvider:
    """``IdentityProvider`` over HTTPS with bounded timeouts and no redirects."""

    def __init__(self, client: httpx.AsyncClient, resolve: Resolver = system_resolver) -> None:
        self._client = client
        self._resolve = resolve

    async def exchange_code(
        self,
        conn: OidcEndpoints,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_secret: str | None,
    ) -> str:
        """POST the code, verifier and redirect URI; return ``id_token`` from the JSON reply."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": conn.client_id,
        }
        if client_secret is not None:
            data["client_secret"] = client_secret
        await require_public_https(conn.token_endpoint, self._resolve)
        try:
            reply = await self._client.post(
                conn.token_endpoint, data=data, follow_redirects=False, timeout=10.0
            )
        except httpx.HTTPError:
            raise IdentityProviderError("idp_unreachable") from None
        if reply.status_code != 200:
            raise IdentityProviderError("idp_refused_code")
        try:
            token = reply.json()["id_token"]
        except (ValueError, KeyError, TypeError):
            raise IdentityProviderError("idp_bad_reply") from None
        if not isinstance(token, str) or not token:
            raise IdentityProviderError("idp_bad_reply")
        return token

    async def jwks(self, conn: OidcEndpoints) -> dict[str, Any]:
        """GET the key set (no redirects)."""
        await require_public_https(conn.jwks_uri, self._resolve)
        try:
            reply = await self._client.get(conn.jwks_uri, follow_redirects=False, timeout=10.0)
            body = reply.json() if reply.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            raise IdentityProviderError("idp_unreachable") from None
        if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
            raise IdentityProviderError("idp_bad_jwks")
        return body
