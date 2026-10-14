"""New upload quarantine objects sit under the top-level ``quarantine/`` prefix, where the
bucket's lifecycle rule can match them, and still resolve to their tenant (TN4)."""

from __future__ import annotations

from uuid import uuid4

from documents.service import document_key, quarantine_key
from tenancy.storage import tenant_of_key, tenant_prefixes


def test_quarantine_keys_use_the_lifecycle_prefix_and_resolve_to_their_tenant() -> None:
    tenant, document = uuid4(), uuid4()
    key = quarantine_key(tenant, document)
    assert key == f"quarantine/tenants/{tenant}/{document}/raw"
    assert tenant_of_key(key) == tenant
    assert tenant_of_key(f"uploads/tenants/{tenant}/x/part-000001") == tenant
    assert tenant_of_key(f"tenants/{tenant}/quarantine/{document}/raw") == tenant  # older rows
    assert tenant_of_key(document_key(tenant, document)) == tenant
    assert tenant_of_key(f"quarantine/tenants/{tenant}/../x") is None


def test_offboarding_sees_every_prefix_of_a_tenant() -> None:
    tenant = uuid4()
    prefixes = tenant_prefixes(tenant)
    for key in (
        quarantine_key(tenant, uuid4()),
        document_key(tenant, uuid4()),
        f"uploads/tenants/{tenant}/u/part-000001",
        f"quarantine/{tenant}/r/i",
    ):
        assert any(key.startswith(p) for p in prefixes), key
    assert not any(str(uuid4()) in p for p in prefixes)
