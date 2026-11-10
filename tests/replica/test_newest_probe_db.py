"""SD-19: the newest-row filters as index probes keep exactly what the NOT EXISTS form kept.

Of the rows of one account that are the same thing (a posting key, a person key, a technology),
only the newest by ``(created_at, id)`` is read; a row with no key (a person without a
``person_key``) is always read. Run on PostgreSQL with planted rows of two runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.replica import current as C
from tests.conftest import ADMIN_DSN
from tests.isolation.matrix.plant import plant, tenant_tables

pytestmark = pytest.mark.requires_postgres
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def _tenant() -> UUID:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tid: UUID = c.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", ("sd19-synthetic",)
        ).fetchone()[0]
    return tid


@pytest.mark.parametrize(
    ("table", "alias", "key", "clause"),
    [
        ("job_postings", "j", "posting_key", C.NEWEST_POSTING),
        ("people", "p", "person_key", C.NEWEST_PERSON),
        ("tech_detections", "t", "technology", C.NEWEST_DETECTION),
    ],
)
def test_the_probe_keeps_only_the_newest_row_of_each_thing(
    table: str, alias: str, key: str, clause: str
) -> None:
    import yaml

    with open("tests/isolation/matrix/matrix.yaml") as f:
        base = (yaml.safe_load(f)["tables"].get(table) or {}).get("plant") or {}
    tenant, account = _tenant(), uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tables = {tb.name: tb for tb in tenant_tables(admin)}
        ids: dict[str, Any] = {}
        for name, value, age in (("old", "k1", 2), ("new", "k1", 1), ("other", "k2", 3)):
            row = plant(
                admin,
                tables[table],
                tenant,
                {**base, "account_id": account, key: value, "run_id": uuid4(),
                 "created_at": T0 - timedelta(days=age)},
            )  # fmt: skip
            ids[name] = row["id"]
        if table == "people":  # a person with no key is always read
            ids["nokey"] = plant(
                admin, tables[table], tenant,
                {**base, "account_id": account, key: None, "run_id": uuid4(), "created_at": T0},
            )["id"]  # fmt: skip
        kept = {
            r[0]
            for r in admin.execute(
                f"SELECT {alias}.id FROM {table} {alias} WHERE {alias}.tenant_id = %s"  # noqa: S608
                f" AND {alias}.account_id = %s{clause}",
                (tenant, account),
            ).fetchall()
        }
    expected = {ids["new"], ids["other"]} | ({ids["nokey"]} if table == "people" else set())
    assert kept == expected
