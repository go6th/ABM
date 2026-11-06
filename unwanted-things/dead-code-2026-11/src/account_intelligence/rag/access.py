"""Access control at the retrieval layer (OWASP LLM02).

Permission enforcement is not a UI filter applied after
retrieval — it is inside the retrieval algorithm itself.
Every chunk carries its ACL, and the retrieved set is
filtered against the caller's identity BEFORE any text
reaches the context window. A cross-department leak is
therefore impossible by construction: the generator
cannot cite what retrieval never returned.

Fail closed is the whole contract: a chunk without an
ACL, a caller without a tenant, a role that does not
match, a clearance below the floor — all drop the chunk.
Zero rows is the correct answer for an unauthorized
caller, and it is indistinguishable from an empty corpus.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from account_intelligence.rag.models import ChunkACL, RetrievedChunk

ADMIN = "admin"


@dataclass(frozen=True)
class Subject:
    """The caller's identity: tenant, roles and clearance."""

    tenant_id: str
    roles: frozenset[str] = frozenset()
    clearance: int = 0

    @property
    def is_admin(self) -> bool:
        """True when the subject holds the admin role."""
        return ADMIN in self.roles


def _allowed(chunk_acl: ChunkACL, subject: Subject) -> bool:
    """One chunk against one subject (fail closed)."""
    if chunk_acl.tenant_id != subject.tenant_id:
        return False
    if subject.is_admin:
        return subject.clearance >= chunk_acl.min_clearance
    if chunk_acl.allowed_roles and not chunk_acl.allowed_roles & subject.roles:
        return False
    return subject.clearance >= chunk_acl.min_clearance


def filter_by_acl(chunks: Iterable[RetrievedChunk], subject: Subject) -> tuple[RetrievedChunk, ...]:
    """Keep only the chunks ``subject`` may see.

    The retrieval surface is limited to the documents
    the authenticated caller has rights to — before the
    context window is populated, not after.
    """
    return tuple(
        chunk for chunk in chunks if chunk.acl is not None and _allowed(chunk.acl, subject)
    )


def assert_same_tenant(chunks: Iterable[RetrievedChunk], subject: Subject) -> None:
    """Raise when any chunk would cross the tenant boundary.

    The belt-and-braces check for the isolation suite:
    retrieval output that survived ``filter_by_acl`` with
    a foreign tenant is a bug, and bugs here are loud.
    """
    for chunk in chunks:
        if chunk.acl is None or chunk.acl.tenant_id != subject.tenant_id:
            raise ValueError(f"chunk {chunk.chunk_id} is outside the caller's tenant")
