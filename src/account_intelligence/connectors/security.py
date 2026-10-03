"""URL validation (the SSRF defence), host allow-lists and secret redaction.

The connectors never fetch a target page themselves: a provider does. The checks
still run before any call so the platform never asks a provider to read an internal
address, and they run again on the final URL a provider reports after redirects.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from account_intelligence.connectors.errors import SecurityError

# Cloud metadata endpoints, refused by name as well as by address range.
METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
        ipaddress.ip_address("100.100.100.200"),
    }
)
METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata", "instance-data"})

Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    """Resolve a host to its addresses (for the SSRF check)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _address_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in METADATA_ADDRESSES:
        return False
    return bool(ip.is_global) and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    )


def check_url_shape(url: str) -> None:
    """Static checks: https only, a host, no userinfo, no IP literal outside public space."""
    if any(ch.isspace() for ch in url):
        raise SecurityError("url contains whitespace")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SecurityError("only https URLs are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SecurityError("URLs with user information are refused")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise SecurityError("url has no host")
    try:
        port = parts.port
    except ValueError as error:
        raise SecurityError("url has an invalid port") from error
    if port not in (None, 443):
        raise SecurityError("only the default https port is allowed")
    if host in METADATA_HOSTS or host.endswith(".internal") or host == "localhost":
        raise SecurityError("internal host names are refused")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return  # a name; resolved and checked by validate_target_url
    if not _address_is_public(host.strip("[]")):
        raise SecurityError("non-public IP address refused")


def host_of(url: str) -> str:
    """Lower-case host of a URL without a trailing dot."""
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def host_allowed(host: str, account_domain: str, extra_allowed: Iterable[str]) -> bool:
    """Account domain and its subdomains, plus explicitly verified hosts (exact match)."""
    host = host.lower().rstrip(".")
    if host == account_domain or host.endswith("." + account_domain):
        return True
    return host in {h.lower().rstrip(".") for h in extra_allowed}


@dataclass(frozen=True)
class UrlPolicy:
    """SSRF policy: https only, public addresses only, no userinfo, metadata address refused."""

    resolver: Resolver = system_resolver
    resolve_timeout_s: float = 3.0


async def validate_target_url(
    url: str,
    policy: UrlPolicy,
    *,
    account_domain: str | None = None,
    extra_allowed: Iterable[str] = (),
) -> None:
    """Full check before a provider is asked to read ``url``.

    ``account_domain`` set means the operation reads a page: the host must be on the
    account's allow-list. DNS must resolve, and every resolved address must be public.
    """
    check_url_shape(url)
    host = host_of(url)
    if account_domain is not None and not host_allowed(host, account_domain, extra_allowed):
        raise SecurityError("host is not on the account allow-list")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return  # literal already checked as public
    except ValueError:
        pass
    try:
        addresses = await asyncio.wait_for(policy.resolver(host), policy.resolve_timeout_s)
    except (OSError, TimeoutError) as error:
        raise SecurityError("host did not resolve") from error
    if not addresses:
        raise SecurityError("host did not resolve")
    if not all(_address_is_public(a) for a in addresses):
        raise SecurityError("host resolves to a non-public address")


def check_final_url(
    requested: str, final: str | None, account_domain: str, extra: Iterable[str]
) -> None:
    """A provider followed redirects: the final page must still be allowed (shape and host)."""
    if final is None or final == requested:
        return
    check_url_shape(final)
    if not host_allowed(host_of(final), account_domain, extra):
        raise SecurityError("provider redirect left the account allow-list")


# --------------------------------------------------------------------------- secrets


class Secret:
    """Holds a credential; never printed, logged or pickled in clear."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("empty secret")
        self._value = value

    def reveal(self) -> str:
        """The secret value (only for building a request header)."""
        return self._value

    def __repr__(self) -> str:
        return "Secret('**********')"

    __str__ = __repr__

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("secrets cannot be pickled")


def load_secret(
    env: dict[str, str] | None, name: str, file_var: str | None = None
) -> Secret | None:
    """From a mounted file (``<NAME>_FILE``) first, then the environment variable."""
    import os

    source = env if env is not None else dict(os.environ)
    path = source.get(file_var or f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            return Secret(handle.read().strip())
    value = source.get(name)
    return Secret(value) if value else None


# Header names whose values never leave the process (requests, cassettes, logs).
SENSITIVE_HEADERS = frozenset(
    {"authorization", "x-api-key", "x-api-token", "cookie", "set-cookie", "proxy-authorization"}
)
KEY_LIKE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._\-]{12,}|sk-(?:live|test)-[a-z0-9]{6,}|fc-[a-z0-9]{16,}|"
    r"\b[a-f0-9]{32,}\b|\b[A-Za-z0-9_\-]{40,}\b)"
)


def redact(text: str, secrets: Iterable[Secret] = ()) -> str:
    """Replace secret values in text (exceptions and recorded cassettes)."""
    for secret in secrets:
        text = text.replace(secret.reveal(), "[REDACTED]")
    return KEY_LIKE.sub("[REDACTED]", text)


def safe_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Headers with sensitive values redacted (for logs and cassettes)."""
    return {k.lower(): ("[REDACTED]" if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers}
