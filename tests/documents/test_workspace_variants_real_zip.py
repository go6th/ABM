"""The owner's own workspace zip, changed the way a later package may differ from it (owner
2026-11-05: "the same only, 90 to 95 percent, a little up and down"), uploaded and read back.
Not part of the suite: it runs only when ``EXACT_COPY_ZIP`` names a zip on this machine (the
owner's packages hold real names), on a scratch database::

    EXACT_COPY_ZIP=~/Downloads/UCB_workspace.zip EXACT_COPY_DOMAIN=ucb.com \\
    VARIANT_REPORT=/tmp/variants.md TEST_DB=ai_test_variants PGPASSWORD=dev ... \\
    .venv/bin/pytest tests/documents/test_workspace_variants_real_zip.py -s

Each variant must keep what the original keeps: every sheet stored as written (new columns and
sheets included), the five files registered by kind, and the people and postings rows. The
synthetic form of the same variants is ``test_workspace_variants_db.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.conftest import _make_account
from tests.documents import workspace_variants as WV
from tests.documents.world import doc_row, service, upload
from tests.fakes.scanner import FakeClamd

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

ZIP = os.environ.get("EXACT_COPY_ZIP")
REPORT = os.environ.get("VARIANT_REPORT")


@pytest.mark.skipif(not ZIP, reason="EXACT_COPY_ZIP not set")
@pytest.mark.parametrize("variant", sorted(WV.VARIANTS))
async def test_a_changed_package_is_stored_as_well_as_the_original(
    tenant_a: UUID, tmp_path: Path, variant: str
) -> None:
    original = Path(ZIP).expanduser().read_bytes()  # type: ignore[arg-type]
    data, expected = WV.VARIANTS[variant](original)
    domain = os.environ.get("EXACT_COPY_DOMAIN") or f"owner-{uuid4().hex[:6]}.example"
    account = _make_account(tenant_a, domain)
    svc = service(tmp_path, scanner=FakeClamd())
    done = await upload(svc, tenant_a, f"{variant}.zip", data, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed", doc_row(doc)
    seen = await WV.measure(tenant_a, account, doc, data, expected, payloads=svc.payloads)
    text = WV.render(variant, seen)
    if REPORT:
        with Path(REPORT).expanduser().open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    assert not seen.problems, seen.problems
