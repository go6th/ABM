"""What was and was not read of each file reaches the file map: an archive's skipped members by
name, members read without any text, a file still being read and one that failed (Ask redesign 3a,
2026-11-02). SYNTHETIC data only.

GUARANTEE(test_coverage_reports_skipped_members_unread_members_and_unfinished_files)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from documents import coverage as COV
from documents import filemap

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
OWNER = "priya-test"


def _seed(tenant: UUID, account: UUID) -> None:
    from tests.conftest import ADMIN_DSN

    def doc(c: Any, name: str, status: str, **extra: Any) -> UUID:
        d = uuid4()
        c.execute(
            "INSERT INTO documents (id, tenant_id, account_id, name, declared_bytes,"
            " declared_type, detected_type, quarantine_key, status, uploaded_by,"
            " upload_expires_at, owner_user_id, stop_detail, object_key, sha256) VALUES (%s, %s,"
            " %s, %s, 10, 'zip', %s, %s, %s, %s, now() + interval '1 day', %s, %s, %s, %s)",
            (
                d,
                tenant,
                account,
                name,
                extra.get("kind", "zip"),
                f"q/{d}",
                status,
                OWNER,
                OWNER,
                extra.get("stop"),
                f"o/{d}",
                hashlib.sha256(str(d).encode()).hexdigest(),
            ),
        )
        return d

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        pack = doc(c, "pack.zip", "indexed")
        c.execute(
            "INSERT INTO document_parts (tenant_id, owner_user_id, document_id, part_no,"
            " start_cursor, status, passages, evidence) VALUES (%s, %s, %s, 0, '{}', 'done', 2,"
            " %s)",
            (
                tenant,
                OWNER,
                pack,
                json.dumps(
                    {
                        "members_read": 3,
                        "skipped": [{"member": "tool.exe", "check": "unsupported_member"}],
                    }
                ),
            ),
        )
        for seq, member in enumerate(("a.pdf", "b.docx"), start=1):
            sha = hashlib.sha256(member.encode()).hexdigest()
            c.execute(
                "INSERT INTO document_passages (tenant_id, account_id, document_id, seq, kind,"
                " location, content, content_sha256, token_count) VALUES (%s, %s, %s, %s,"
                " 'text', %s, 'text', %s, 1)",
                (tenant, account, pack, seq, json.dumps({"member": member}), sha),
            )
        doc(c, "late.pdf", "parsing", kind="pdf")
        doc(c, "bad.pdf", "failed", kind="pdf", stop="parts_failed")
        doc(c, "plain.xlsx", "indexed", kind="xlsx")


async def test_coverage_reports_skipped_members_unread_members_and_unfinished_files(
    world: dict[str, Any],
) -> None:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN) as c:
        account = UUID(
            str(
                c.execute(
                    "SELECT id FROM accounts WHERE tenant_id = %s ORDER BY domain LIMIT 1",
                    (world["a"],),
                ).fetchone()[0]  # type: ignore[index]
            )
        )
    _seed(world["a"], account)
    async with tenant_txn(world["a"], user=OWNER) as s:
        entries = await COV.coverage(s, [account])
    by = {e["document"]: e for e in entries}
    pack = by["pack.zip"]
    assert pack["members_read"] == 3
    assert pack["members_skipped"] == [{"member": "tool.exe", "reason": "not a document type"}]
    assert pack["members_no_text"] == 1  # three read, two stored text
    assert by["late.pdf"]["status"] == "parsing" and by["bad.pdf"]["status"] == "failed"
    assert "members_read" not in by["plain.xlsx"]

    text = filemap.render([], [], entries)
    assert 'ARCHIVE "pack.zip": 3 files read, 1 skipped (tool.exe: not a document type)' in text
    assert "1 read but held no text" in text
    assert '"late.pdf": still being read' in text and '"bad.pdf": could not be read' in text
    assert "plain.xlsx" not in text  # a file read in full needs no line
    assert "say so in one line" in text


def test_a_workspace_of_fully_read_plain_files_has_no_coverage_section() -> None:
    plain = [{"document": "a.xlsx", "status": "indexed", "type": "xlsx"}]
    assert filemap.render([], [], plain) == ""
