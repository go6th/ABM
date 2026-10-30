"""The raw file store (migration 0078; documents/service.py and repo.py): the uploaded bytes are
the golden record, kept content-addressed. One object per (tenant, sha256): uploading the same
file twice registers no second copy and the second document still indexes; the stored object is
byte-for-byte the upload (raw fidelity); a changed file is a new hash, so a superseded version is
kept; deleting removes the shared object and its registry row only with the last document.
SYNTHETIC files only."""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from research_files.blobstore import BlobMissing
from tests.conftest import _make_account
from tests.documents import fixtures
from tests.documents.world import admin, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@contextlib.contextmanager
def _as_user(user: str) -> Iterator[None]:
    """A request of one user: user scope (the tests default to the system scope of a worker)."""
    from account_intelligence.db.session import _SYSTEM, acting_as, set_user_scope

    token = set_user_scope()
    try:
        with acting_as(user):
            yield
    finally:
        _SYSTEM.reset(token)


def _raw_rows(sha: str) -> list[dict[str, object]]:
    return [
        {"object_key": r[0], "size": r[1]}
        for r in admin("SELECT object_key, size_bytes FROM raw_files WHERE sha256 = %s", sha)
    ]


async def test_identical_uploads_share_one_raw_object_until_the_last_document_goes(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """Two uploads of the same bytes: one object and one registry row; fidelity exact; the
    object survives the first delete, not the second; changed bytes are a new object (see the
    module docstring)."""
    account = _make_account(tenant_a, f"acme-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    data = fixtures.notes_docx()
    sha = hashlib.sha256(data).hexdigest()
    first = await upload(svc, tenant_a, "notes.docx", data, account)
    second = await upload(svc, tenant_a, "notes-again.docx", data, account)
    assert await svc.process(tenant_a, UUID(first["document_id"])) == "indexed"
    assert await svc.process(tenant_a, UUID(second["document_id"])) == "indexed"
    (row,) = _raw_rows(sha)
    key = f"tenants/{tenant_a}/raw/{sha}"
    assert row["object_key"] == key and int(row["size"]) == len(data)  # one copy, idempotent
    with svc.blobs.open(key) as raw:
        assert raw.read() == data  # the stored bytes are exactly the uploaded ones
    for doc in (first, second):
        (object_key,) = admin("SELECT object_key FROM documents WHERE id = %s", doc["document_id"])[
            0
        ]
        assert object_key == key  # both documents point at the shared object
    changed_data = fixtures.notes_docx("Revised.")
    changed = await upload(svc, tenant_a, "notes-v2.docx", changed_data, account)
    assert await svc.process(tenant_a, UUID(changed["document_id"])) == "indexed"
    changed_sha = hashlib.sha256(changed_data).hexdigest()
    assert changed_sha != sha and len(_raw_rows(changed_sha)) == 1  # the superseded file stays,
    assert len(_raw_rows(sha)) == 1  # and so does the first version

    await svc.delete(tenant_a, UUID(first["document_id"]), "user-1")
    assert _raw_rows(sha) and await svc.blobs.size(key) == len(data)  # the share keeps the object
    await svc.delete(tenant_a, UUID(second["document_id"]), "user-1")
    assert not _raw_rows(sha)
    with pytest.raises(BlobMissing):
        await svc.blobs.size(key)
    assert len(_raw_rows(changed_sha)) == 1  # the other version is untouched

    again = await upload(svc, tenant_a, "notes-back.docx", data, account)
    assert await svc.process(tenant_a, UUID(again["document_id"])) == "indexed"
    assert _raw_rows(sha) and await svc.blobs.size(key) == len(data)  # re-uploading stores it again


async def _upload_as(
    svc: object, tenant: UUID, user: str, name: str, data: bytes, account: UUID
) -> dict[str, object]:
    """``world.upload`` for another user of the same tenant."""
    from urllib.parse import parse_qs, urlparse

    started = await svc.start_upload(tenant, user, name, len(data), None, account)  # type: ignore[attr-defined]

    async def body() -> object:
        yield data

    token = parse_qs(urlparse(started["upload_url"]).query)["token"][0]
    await svc.receive(tenant, UUID(started["document_id"]), token, body())  # type: ignore[attr-defined]
    return await svc.complete(tenant, UUID(started["document_id"]))  # type: ignore[attr-defined,no-any-return]


async def test_a_delete_never_removes_the_object_a_colleagues_document_points_at(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """F6 (leakage review 2026-10-30): two users of one client upload the same file into their
    private workspaces. One object is shared; each user sees only their own document, so the
    delete of the first must still count the colleague's before it removes the object."""
    # each user researches into their own private workspace (an account of their own)
    mine = _make_account(tenant_a, f"mine-{uuid4().hex[:6]}.example")
    theirs = _make_account(tenant_a, f"theirs-{uuid4().hex[:6]}.example")
    admin("UPDATE accounts SET owner_user_id = 'user-1' WHERE id = %s", mine)
    admin("UPDATE accounts SET owner_user_id = 'user-2' WHERE id = %s", theirs)
    svc = service(tmp_path)
    data = fixtures.notes_docx()
    sha = hashlib.sha256(data).hexdigest()
    key = f"tenants/{tenant_a}/raw/{sha}"
    with _as_user("user-1"):
        first = await _upload_as(svc, tenant_a, "user-1", "sheet.docx", data, mine)
    with _as_user("user-2"):
        second = await _upload_as(svc, tenant_a, "user-2", "sheet-too.docx", data, theirs)
    assert await svc.process(tenant_a, UUID(str(first["document_id"]))) == "indexed"
    assert await svc.process(tenant_a, UUID(str(second["document_id"]))) == "indexed"
    with _as_user("user-1"):
        await svc.delete(tenant_a, UUID(str(first["document_id"])), "user-1")
    assert _raw_rows(sha) and await svc.blobs.size(key) == len(data)  # user-2 still has it
    with svc.blobs.open(key) as raw:
        assert raw.read() == data
    with _as_user("user-2"):
        await svc.delete(tenant_a, UUID(str(second["document_id"])), "user-2")
    assert not _raw_rows(sha)  # the last one takes the object with it
    with pytest.raises(BlobMissing):
        await svc.blobs.size(key)
