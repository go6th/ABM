"""The create-only CRM client (CW1, D-77). The interface exposes creates and read-only lookups
ONLY: there is no update, upsert, merge or delete method, and ``assert_create_only`` (run at
import of every adapter and by the tests) refuses any client class that grows one.

GUARANTEE(test_the_crm_client_cannot_overwrite_or_delete)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Kind = Literal["note", "task", "account_snapshot"]
FORBIDDEN = (
    "update",
    "upsert",
    "merge",
    "delete",
    "remove",
    "patch",
    "put",
    "overwrite",
    "replace",
    "destroy",
    "edit",
    "modify",
    "set_field",
    "undelete",
)
ALLOWED = frozenset({"create", "find_by_marker", "find_account", "kind"})


@dataclass(frozen=True)
class Created:
    """The CRM's answer to a create."""

    record_id: str


@dataclass(frozen=True)
class Draft:
    """A rendered, scanned record to create."""

    kind: Kind
    account_record_id: str
    title: str
    body: str
    marker: str


class CrmClient(Protocol):
    """Creates and read-only lookups; nothing else."""

    kind: str

    async def create(self, draft: Draft) -> Created:
        """Create one NEW record attached to the account record."""

    async def find_by_marker(self, marker: str) -> Created | None:
        """A record previously created with this marker (read only)."""

    async def find_account(self, domain: str) -> list[str]:
        """Candidate account record ids for a domain (read only)."""


class NotCreateOnly(TypeError):
    """A client class exposes a method that could change or remove an existing record."""


def assert_create_only(cls: type) -> type:
    """Refuse a client class with any public method outside the allow-list or named like a
    write to an existing record."""
    public = {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n, None))}
    bad = sorted(n for n in public if n not in ALLOWED or any(f in n.lower() for f in FORBIDDEN))
    if bad:
        raise NotCreateOnly(f"{cls.__name__} exposes {bad}")
    return cls
