"""The repair stage's readers against the database: a source stored without a title takes the
title the link archive captured for the page, and a tech detection whose source_ref is the page's
address (as the detection step stores it) resolves to that address (workspace repair,
2026-10-31)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.urls import normalize_url, url_hash
from exports.repair import readers as RD
from tests.conftest import ADMIN_DSN

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
PAGE = "https://news.example.org/2026/10/12/acme-names-a-new-officer"
OTHER = "https://news.example.org/untitled-page"


def _seed(tenant: Any, account: Any) -> None:
    norm, other = normalize_url(PAGE), normalize_url(OTHER)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for url, norm_url, code in ((PAGE, norm, "S-9001"), (OTHER, other, "S-9002")):
            c.execute(
                "INSERT INTO sources (tenant_id, account_id, source_code, url, url_normalized,"
                " url_hash, kind, first_seen_account_run_id, last_seen_account_run_id)"
                " SELECT %s, %s, %s, %s, %s, %s, 'secondary', id, id FROM account_runs"
                " WHERE account_id = %s LIMIT 1",
                (tenant, account, code, url, norm_url, url_hash(norm_url), account),
            )
        for kind, title in (("page", "Acme names a new officer"), ("profile", "Some Person")):
            c.execute(
                "INSERT INTO link_archive (tenant_id, owner_user_id, account_id, url,"
                " url_normalized, url_hash, kind, payload_ref, title, archived_at)"
                " VALUES (%s, 'priya-test', %s, %s, %s, %s, %s, %s, %s, now())",
                (tenant, account, PAGE if kind == "page" else OTHER,
                 norm if kind == "page" else other,
                 url_hash(norm if kind == "page" else other), kind, f"ref-{uuid4().hex}", title),
            )  # fmt: skip
        c.execute(
            "INSERT INTO tech_detections (tenant_id, account_id, run_id, technology, first_seen_at,"
            " last_seen_at, source_count, source_ref) SELECT %s, %s, id, 'Some Platform',"
            " '2026-01-01', '2026-02-01', 1, %s FROM account_runs WHERE account_id = %s LIMIT 1",
            (tenant, account, PAGE, account),
        )


async def test_titles_come_from_the_archive_and_a_url_source_ref_resolves(
    world: dict[str, Any],
) -> None:
    from account_intelligence.api.routes.account_workspace import account_id_of
    from account_intelligence.db.session import acting_as

    with acting_as("priya-test"):
        async with tenant_txn(world["a"], user="priya-test", system=False) as s:
            account = await account_id_of(s, "highmark.example")
        _seed(world["a"], account)
        async with tenant_txn(world["a"], user="priya-test", system=False) as s:
            sources = {r.url: r for r in await RD.read_sources(s, account)}
            tech = await RD.read_tech(s, account)
    assert sources[PAGE].title == "Acme names a new officer"
    assert sources[OTHER].title == ""  # only a profile copy has a title: it is never lent
    assert [t.source_url for t in tech if t.technology == "Some Platform"] == [PAGE]
