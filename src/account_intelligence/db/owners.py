"""Act as the owner of one owned item (private workspaces, migration 0063).

A worker task or command about one run, document, job or download reads the item's owner as a system
job, then acts as that user in user scope for the rest of its context, so the database confines
the work to that user's rows.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import set_actor, set_user_scope, tenant_txn

OWNED_ITEMS = {
    "run": "account_runs",
    "document": "documents",
    "job": "research_jobs",
    "download": "download_jobs",
}


class ItemNotFound(LookupError):
    """The item is not in the tenant."""


async def act_as_owner_of(kind: str, tenant_id: UUID, item_id: UUID) -> str:
    """Act as the owner of one item for the rest of this context; returns the owner."""
    table = OWNED_ITEMS[kind]
    async with tenant_txn(tenant_id, system=True) as s:
        owner = (
            await s.execute(
                text(f"SELECT owner_user_id FROM {table} WHERE id = :i"),  # noqa: S608 fixed names
                {"i": item_id},
            )
        ).scalar_one_or_none()
    if owner is None:
        raise ItemNotFound(f"{kind} not found")
    set_actor(str(owner))
    set_user_scope()
    return str(owner)
