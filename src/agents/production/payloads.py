"""``PayloadPort`` on the platform payload store (neutral, content-addressed keys).

A source item is stored as JSON under ``tenants/<tenant>/accounts/<account>/runs/<run>/raw/
<sha256>.json.gz`` (the connector layer's key scheme: no provider name in any key). ``view``
refuses a key outside the caller's tenant prefix (KeyError), so a forged reference cannot read
another tenant's payload even in a shared bucket. With an ``archive`` the stored item is also
recorded as the archived copy of its address (``account_intelligence.archive``): the item body is
the copy, so a row that cites the page can open it once the live link dies. Private items (the
client's own mail, notes and files) are never archived for the client screens.

GUARANTEE(test_store_payloads_round_trip_and_refuse_other_tenants)
GUARANTEE(test_a_stored_link_is_archived_and_served_back)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from account_intelligence.archive import ArchiveEntry, LinkArchive, link_kind, title_of
from account_intelligence.connectors.payloads import payload_key
from account_intelligence.platform.ports import PayloadStore
from agents.ports import RunContext, SourceItem
from agents.schemas import PayloadView


@dataclass(frozen=True)
class StorePayloads:
    """See the module docstring. One instance per run (it knows the account)."""

    store: PayloadStore
    account_id: UUID
    archive: LinkArchive | None = None

    async def put(self, ctx: RunContext, row_key: str, item: SourceItem, visibility: str) -> str:
        """Store one item; the key is its content address (a replay stores nothing new). A
        public item is recorded as the archived copy of its address."""
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
        ref = await self.store.put(key, body)
        if self.archive is not None and visibility != "private" and item.url:
            await self.archive.record(
                [
                    ArchiveEntry(
                        url=item.url,
                        kind=link_kind(item.url, "feed_item" if item.match_kind else "page"),
                        payload_ref=ref,
                        archived_at=self.archive.now(),
                        format="json",
                        title=title_of(item.text),
                    )
                ]
            )
        return ref

    async def view(self, tenant_id: UUID, ref: str) -> PayloadView:
        """The stored item as a quarantined view (KeyError outside the tenant's prefix)."""
        if not ref.startswith(f"tenants/{tenant_id}/"):
            raise KeyError("payload outside this tenant")
        data = json.loads(await self.store.get(ref))
        return PayloadView.model_validate({**data, "payload_ref": ref})
