"""Proof that a tenant owns an email domain before its SSO connection routes that domain (SD-10).

A tenant admin lists the domains of an IdP connection. Before this, the list was trusted as typed,
so a tenant could claim another company's domain. Now every listed domain needs a DNS TXT record
at ``_6th-sso.<domain>`` whose value is ``6th-sso-verification=<token>``, where the token is a
hash of the saving tenant's id and the domain. Only whoever controls the domain's DNS can publish
it, and a record published for one tenant proves nothing for another, so the token needs no
secret and no stored state. The check runs on every save of a connection.

GUARANTEE(test_a_tenant_cannot_claim_a_domain_without_its_dns_record)
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

RECORD_PREFIX = "_6th-sso"
VALUE_PREFIX = "6th-sso-verification="
LOOKUP_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class Record:
    """The TXT record that proves one domain for one tenant."""

    name: str
    value: str


class DomainUnproven(Exception):
    """A listed domain has no matching TXT record; ``missing`` names the records to publish."""

    def __init__(self, missing: Sequence[Record]) -> None:
        super().__init__("domain ownership is not proven")
        self.missing = list(missing)


def expected(tenant_id: UUID, domain: str) -> Record:
    """The record this tenant must publish to prove ``domain``."""
    domain = domain.strip().lower().rstrip(".")
    token = hashlib.sha256(f"6th-sso:{tenant_id}:{domain}".encode()).hexdigest()[:40]
    return Record(f"{RECORD_PREFIX}.{domain}", VALUE_PREFIX + token)


async def txt_records(name: str) -> list[str]:
    """The TXT strings at ``name`` (empty when there is none or the lookup fails)."""
    import dns.asyncresolver
    import dns.exception

    try:
        answer = await dns.asyncresolver.resolve(name, "TXT", lifetime=LOOKUP_TIMEOUT_S)
    except dns.exception.DNSException:
        return []
    out = []
    for rdata in answer:
        strings = getattr(rdata, "strings", ())
        out.append(b"".join(strings).decode("utf-8", "replace").strip())
    return out


async def prove(tenant_id: UUID, domains: Sequence[str]) -> None:
    """``DomainUnproven`` unless every domain carries this tenant's record."""
    missing = []
    for domain in domains:
        record = expected(tenant_id, domain)
        if record.value not in await txt_records(record.name):
            missing.append(record)
    if missing:
        raise DomainUnproven(missing)
