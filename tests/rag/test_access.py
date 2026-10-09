"""Retrieval-layer access control (fail closed)."""

from __future__ import annotations

import pytest

from account_intelligence.rag.access import (
    Subject,
    assert_same_tenant,
    filter_by_acl,
)
from account_intelligence.rag.models import ChunkACL, RetrievedChunk


def _chunk(
    chunk_id: str,
    *,
    tenant: str = "t1",
    roles: frozenset[str] = frozenset({"analyst"}),
    clearance: int = 0,
) -> RetrievedChunk:
    """One chunk carrying an ACL."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=f"src-{chunk_id}",
        text=f"text of {chunk_id}",
        score=0.5,
        acl=ChunkACL(tenant_id=tenant, allowed_roles=roles, min_clearance=clearance),
    )


class TestSubject:
    def test_admin_role(self) -> None:
        """The admin role is recognized."""
        assert Subject("t1", roles=frozenset({"admin"})).is_admin
        assert not Subject("t1", roles=frozenset({"analyst"})).is_admin

    def test_defaults(self) -> None:
        """A subject with no roles and no clearance is valid."""
        subject = Subject("t1")
        assert subject.roles == frozenset()
        assert subject.clearance == 0


class TestFilterByAcl:
    def test_same_tenant_matching_role(self) -> None:
        """A matching tenant and role keeps the chunk."""
        chunks = (_chunk("a"),)
        subject = Subject("t1", roles=frozenset({"analyst"}))
        assert filter_by_acl(chunks, subject) == chunks

    def test_foreign_tenant_dropped(self) -> None:
        """A chunk from another tenant never reaches the context."""
        chunks = (_chunk("a", tenant="t2"),)
        subject = Subject("t1", roles=frozenset({"analyst"}))
        assert filter_by_acl(chunks, subject) == ()

    def test_missing_role_dropped(self) -> None:
        """A caller without a required role is dropped."""
        chunks = (_chunk("a", roles=frozenset({"admin"})),)
        subject = Subject("t1", roles=frozenset({"analyst"}))
        assert filter_by_acl(chunks, subject) == ()

    def test_admin_bypasses_roles(self) -> None:
        """An admin passes role checks but not clearance floors."""
        chunks = (_chunk("a", roles=frozenset({"admin"})),)
        subject = Subject("t1", roles=frozenset({"admin"}))
        assert filter_by_acl(chunks, subject) == chunks

    def test_clearance_floor(self) -> None:
        """Clearance below the chunk's floor is dropped."""
        chunks = (_chunk("a", clearance=3),)
        subject_below_floor = Subject("t1", roles=frozenset({"analyst"}), clearance=2)
        subject_at_floor = Subject("t1", roles=frozenset({"analyst"}), clearance=3)
        assert filter_by_acl(chunks, subject_below_floor) == ()
        assert filter_by_acl(chunks, subject_at_floor) == chunks

    def test_open_acl_keeps_any_role(self) -> None:
        """A chunk with no role restriction matches any role."""
        chunks = (_chunk("a", roles=frozenset()),)
        subject = Subject("t1", roles=frozenset({"anyone"}))
        assert filter_by_acl(chunks, subject) == chunks

    def test_chunk_without_acl_dropped(self) -> None:
        """A chunk with no ACL fails closed."""
        chunk = RetrievedChunk(chunk_id="a", source_id="src-a", text="t", score=0.5)
        assert filter_by_acl((chunk,), Subject("t1")) == ()

    def test_zero_rows_for_unauthorized_caller(self) -> None:
        """An unauthorized caller gets an empty, indistinguishable result."""
        chunks = tuple(_chunk(str(i), tenant="other") for i in range(5))
        subject = Subject("t1", roles=frozenset({"analyst"}))
        assert filter_by_acl(chunks, subject) == ()


class TestAssertSameTenant:
    def test_same_tenant_passes(self) -> None:
        """Chunks of the caller's tenant pass the belt-and-braces check."""
        chunks = (_chunk("a"), _chunk("b"))
        assert_same_tenant(chunks, Subject("t1"))

    def test_foreign_tenant_raises(self) -> None:
        """A foreign chunk is loud, not silent."""
        chunks = (_chunk("a"), _chunk("b", tenant="t2"))
        with pytest.raises(ValueError, match="outside the caller's tenant"):
            assert_same_tenant(chunks, Subject("t1"))

    def test_missing_acl_raises(self) -> None:
        """A chunk without an ACL cannot be verified."""
        chunk = RetrievedChunk(chunk_id="a", source_id="src-a", text="t", score=0.5)
        with pytest.raises(ValueError):
            assert_same_tenant((chunk,), Subject("t1"))
