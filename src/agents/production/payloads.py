"""``PayloadPort`` on the platform payload store (neutral, content-addressed keys).

A source item is stored as JSON under ``tenants/<tenant>/accounts/<account>/runs/<run>/raw/
<sha256>.json.gz`` (the connector layer's key scheme: no provider name in any key). ``view``
refuses a key outside the caller's tenant prefix (KeyError), so a forged reference cannot read
another tenant's payload even in a shared bucket.

GUARANTEE(test_store_payloads_round_trip_and_refuse_other_tenants)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from account_intelligence.connectors.payloads import payload_key
from account_intelligence.platform.ports import PayloadStore
from agents.ports import RunContext, SourceItem
from agents.schemas import PayloadView


@dataclass(frozen=True)
class StorePayloads:
    """See the module docstring. One instance per run (it knows the account)."""

    store: PayloadStore
    account_id: UUID

    async def put(self, ctx: RunContext, row_key: str, item: SourceItem, visibility: str) -> str:
        """Store one item; the key is its content address (a replay stores nothing new)."""
        body = json.dumps(
            {
                "row_key": row_key,
                "url": item.url,
                "text": item.text,
                "visibility": "private" if visibility == "private" else "public",
                "match_kind": item.match_kind,
            },
            sort_keys=True,
        ).encode()
        key = payload_key(ctx.tenant_id, self.account_id, ctx.run_id, body)
        return await self.store.put(key, body)

    async def view(self, tenant_id: UUID, ref: str) -> PayloadView:
        """The stored item as a quarantined view (KeyError outside the tenant's prefix)."""
        if not ref.startswith(f"tenants/{tenant_id}/"):
            raise KeyError("payload outside this tenant")
        data = json.loads(await self.store.get(ref))
        return PayloadView.model_validate({**data, "payload_ref": ref})
