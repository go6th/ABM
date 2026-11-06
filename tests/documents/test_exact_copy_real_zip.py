"""The owner's own workspace zip through the upload, read back and compared with the zip
(exact-copy check, 2026-11-05). Not part of the suite: it runs only when ``EXACT_COPY_ZIP``
names a zip on this machine (the owner's packages hold real names), on a scratch database::

    EXACT_COPY_ZIP=~/Downloads/UCB_workspace.zip EXACT_COPY_DOMAIN=ucb.com \\
    EXACT_COPY_REPORT=/tmp/report.md TEST_DB=ai_test_exact PGPASSWORD=dev ... \\
    .venv/bin/pytest tests/documents/test_exact_copy_real_zip.py -s

The report lists, per workbook sheet, the rows and columns the zip holds against what the
table store holds, and the native rows (people, postings) against the sheets, naming every row
that did not come back. The synthetic form of the same check is
``test_workspace_exact_copy_db.py``; this file is for the owner's real packages.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.conftest import _make_account
from tests.documents.world import doc_row, service, upload
from tests.fakes.scanner import FakeClamd

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

ZIP = os.environ.get("EXACT_COPY_ZIP")
REPORT = os.environ.get("EXACT_COPY_REPORT")


@pytest.mark.skipif(not ZIP, reason="EXACT_COPY_ZIP not set")
async def test_the_owners_zip_comes_back_whole(tenant_a: UUID, tmp_path: Path) -> None:
    from documents import exact_copy as EC

    data = Path(ZIP).expanduser().read_bytes()  # type: ignore[arg-type]
    domain = os.environ.get("EXACT_COPY_DOMAIN") or f"owner-{uuid4().hex[:6]}.example"
    account = _make_account(tenant_a, domain)
    # the signature guard refuses the build scripts inside an owner's zip; staging's clamd
    # passes them, so the content scanner alone stands in here
    svc = service(tmp_path, scanner=FakeClamd())
    done = await upload(svc, tenant_a, Path(ZIP).name, data, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed", doc_row(doc)
    report = await EC.report(tenant_a, account, doc, data, payloads=svc.payloads)
    text = EC.render(report)
    if REPORT:
        Path(REPORT).expanduser().write_text(text, encoding="utf-8")
    print(text)
    assert not report.differences, f"{len(report.differences)} difference(s); see the report"
