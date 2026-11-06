"""A changed workspace package is stored as well as the original (owner 2026-11-05: later
packages are "90 to 95 percent the same, a little up and down"). The synthetic owner-shaped zip
(``workspace_zip_owner``) goes through the upload as it is and changed six ways
(``workspace_variants``: renamed files, no marker, flat, renamed and new columns, a new sheet,
all together); each must keep every table cell for cell, register its five files by kind and
store every people and posting row. The owner's own zip is checked the same way by hand
(``test_workspace_variants_real_zip.py``)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from tests.conftest import _make_account
from tests.documents import workspace_variants as WV
from tests.documents import workspace_zip_owner as OWNER
from tests.documents.world import doc_row, service, upload
from tests.fakes.scanner import FakeClamd

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("variant", sorted(WV.VARIANTS))
async def test_a_changed_package_is_stored_as_well_as_the_original(
    tenant_a: UUID, tmp_path: Path, variant: str
) -> None:
    data, expected = WV.VARIANTS[variant](OWNER.workspace_zip_bytes())
    assert len(expected) == 5, expected
    account = _make_account(tenant_a, OWNER.WORKSPACE_DOMAIN)
    svc = service(tmp_path, scanner=FakeClamd())
    done = await upload(svc, tenant_a, f"{variant}.zip", data, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed", doc_row(doc)
    seen = await WV.measure(tenant_a, account, doc, data, expected, payloads=svc.payloads)
    assert not seen.problems, WV.render(variant, seen)
