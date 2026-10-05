"""Validate run domains before any database reservation or workflow dispatch.

Guarantees: obvious non-hostnames, blocked entities, and non-public DNS answers
fail before spend (GUARANTEE(test_domain_validation_fails_before_any_run_write)).
Resolver implementations must use allow-listed egress and one bounded lookup.
"""

from __future__ import annotations

import ipaddress
import re

from account_intelligence.api.errors import APIProblem, NotConfigured
from account_intelligence.api.ports import BlockedEntityPort, DomainResolverPort

_DOMAIN_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$")


class UnconfiguredDomainResolver:
    """Fail closed until a production allow-listed resolver is supplied."""

    async def resolve_public(self, domain: str) -> tuple[str, ...]:
        """Refuse to resolve without an allow-listed resolver so no rogue egress happens."""
        raise NotConfigured("allow-listed domain resolver")


class UnconfiguredBlockedEntityPort:
    """Fail closed until the blocked-entity policy is supplied."""

    async def is_blocked(self, domain: str) -> bool:
        """Refuse to answer without the reviewed blocked-entity policy."""
        raise NotConfigured("blocked-entity policy")


async def validate_domain(
    domain: str,
    *,
    resolver: DomainResolverPort,
    blocked_entities: BlockedEntityPort,
) -> str:
    """Validate normalized host syntax, blocklist, and resolved public IPs."""
    if (
        not domain
        or len(domain) > 253
        or domain != domain.lower()
        or not domain.isascii()
        or domain.endswith(".")
        or domain == "localhost"
        or domain.endswith((".localhost", ".local", ".internal"))
        or any(label.startswith("xn--") for label in domain.split("."))
        or any(label.startswith("-") or label.endswith("-") for label in domain.split("."))
        or not _DOMAIN_PATTERN.fullmatch(domain)
    ):
        raise APIProblem(422, "invalid_domain", "Invalid domain", "A public hostname is required.")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise APIProblem(
            422,
            "invalid_domain",
            "Invalid domain",
            "IP address literals are not allowed.",
        )

    if await blocked_entities.is_blocked(domain):
        raise APIProblem(422, "blocked_domain", "Invalid domain", "This domain cannot be used.")

    addresses = await resolver.resolve_public(domain)
    if not addresses:
        raise APIProblem(
            422,
            "domain_unresolved",
            "Invalid domain",
            "The domain could not be resolved to a public address.",
        )
    try:
        resolved = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError:
        raise APIProblem(
            422,
            "domain_unresolved",
            "Invalid domain",
            "The domain is invalid.",
        ) from None
    if any(not address.is_global for address in resolved):
        raise APIProblem(
            422,
            "non_public_domain",
            "Invalid domain",
            "The domain resolves to a non-public address.",
        )
    return domain
