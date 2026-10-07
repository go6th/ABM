"""Salesforce adapter (D-77): create-only by construction (``assert_create_only``). No connected
app is registered, so every call raises ``NotConfigured``; the shape is the create and read-only
lookups of ``crm_writeback.client.CrmClient`` ([VERIFY] the REST create endpoints and sandbox
behaviour before building the HTTP calls)."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured
from crm_writeback.client import Created, Draft, assert_create_only


@assert_create_only
class SalesforceCrm:
    """Creates notes, tasks and snapshot records; reads accounts and markers."""

    kind = "salesforce"

    async def create(self, draft: Draft) -> Created:
        """Create one new record (not configured)."""
        raise NotConfigured("no Salesforce connected app is registered")

    async def find_by_marker(self, marker: str) -> Created | None:
        """Read only (not configured)."""
        raise NotConfigured("no Salesforce connected app is registered")

    async def find_account(self, domain: str) -> list[str]:
        """Read only (not configured)."""
        raise NotConfigured("no Salesforce connected app is registered")
