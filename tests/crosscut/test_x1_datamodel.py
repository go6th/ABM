"""X1 data model (DM1 to DM7, roadmap 11.1). Lying scenarios: a removed column, a missing check, a
table without forced RLS, a run whose config changes mid-run, a pattern-built careers URL, a fact
without evidence or a Hypothesis without its question, a count without its base, a date range
treated as proof, and every way an export file can drift from its rows."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from crosscut import conformance
from datamodel import careers, counts, export_verify, pins
from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, uid

from .conftest import NOW, db, tenant_with_account

ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> conformance.Catalog:
    with psycopg.connect(ADMIN_DSN) as c:
        return conformance.load_catalog(c)


# --- DM1: the executable spec ------------------------------------------------------------------


@pytest.mark.requires_postgres
def test_the_data_model_matches_its_executable_spec() -> None:
    spec = yaml.safe_load((CONFIG_DIR / "data_model_spec.yaml").read_text())
    cat = _catalog()
    with psycopg.connect(ADMIN_DSN) as c:
        nullable = {
            f"{t}.{col}"
            for t, col in c.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE"
                " table_schema = 'public' AND is_nullable = 'YES'"
            ).fetchall()
        }
        # a VALIDATED check "(col IS NOT NULL)" is the same guarantee as NOT NULL
        for t, d in c.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE contype = 'c' AND convalidated"
        ).fetchall():
            m = __import__("re").fullmatch(r"CHECK \(\((\w+) IS NOT NULL\)\)", d)
            if m:
                nullable.discard(f"{t}.{m.group(1)}")
    problems = []
    for name, e in spec["entities"].items():
        t = e["table"]
        for col in e["keys"] + e.get("fields", []):
            if f"{t}.{col}" not in cat.columns:
                problems.append(f"{name}: {t}.{col} missing")
        for col in e["keys"]:
            if f"{t}.{col}" in nullable:
                problems.append(f"{name}: key {t}.{col} is nullable")
        if not e.get("platform"):
            if f"{t}.tenant_id" not in cat.columns:
                problems.append(f"{name}: {t} has no tenant column")
            if t not in cat.forced_rls:
                problems.append(f"{name}: {t} has no forced RLS")
    assert problems == []


@pytest.mark.requires_postgres
def test_the_conformance_checker_finds_planted_gaps() -> None:
    cat = _catalog()
    spec = {
        "requirements": {
            "A": {
                "text": "a",
                "evidence": [{"column": "accounts.careers_fetch_ref"}, {"rls": "accounts"}],
            },
            "B": {"text": "b", "evidence": [{"check": "facts:ck_facts_hypothesis_has_question"}]},
            "C": {
                "text": "c",
                "evidence": [
                    {"symbol": "datamodel.counts:render_count"},
                    {"symbol": "nope.module:x"},
                ],
            },
            "D": {"text": "d", "evidence": [{"file": "does/not/exist"}], "blocked_by": "src/nope"},
        }
    }
    ok = {r.id: r.status for r in conformance.evaluate(spec, cat)}
    assert ok == {"A": "Done", "B": "Done", "C": "Partial", "D": "Blocked-by-dependency"}
    planted = conformance.Catalog(
        cat.columns - {"accounts.careers_fetch_ref"},
        cat.checks - {"facts:ck_facts_hypothesis_has_question"},
        cat.forced_rls - {"accounts"},
    )
    got = {r.id: r.status for r in conformance.evaluate(spec, planted)}
    assert got["A"] == "Missing" and got["B"] == "Missing"  # removed column, check and RLS
    with pytest.raises(ValueError):
        conformance.present({"wrong": "x"}, cat)
    report = conformance.render(conformance.evaluate(spec, cat), "T")
    assert "| `C` | c | Partial |" in report
    assert all(
        r.status in ("Done", "Partial", "Missing", "Blocked-by-dependency")
        for r in conformance.evaluate(conformance.load_spec(), cat)
    )


# --- DM2: runs are pinned ----------------------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_run_stays_pinned_when_config_changes_mid_run(
    plane: Plane, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    t, _ = tenant_with_account("x1-pins")
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    r = await browser.call(
        "POST",
        "/v1/runs",
        json={"domain": "pinned.example"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert r.status_code == 202, r.text
    run = UUID(r.json()["run_id"])
    async with tenant_txn(t) as s:
        before = await pins.of_run(s, run)
    assert before.checklist_version == pins.checklist_version()
    assert before.model_versions == pins.model_versions() and before.graph_version
    monkeypatch.setattr(pins, "checklist_version", lambda path=None: "0" * 64)  # config changed
    async with tenant_txn(t) as s:
        assert await pins.of_run(s, run) == before  # the run reads its pins, not the files
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for sql in (
            "UPDATE account_runs SET checklist_version = 'x' WHERE id = %s",
            "UPDATE account_runs SET model_versions = '{}' WHERE id = %s",
            "UPDATE account_runs SET graph_version = 'g2' WHERE id = %s",
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(sql, (run,))


# --- DM3: careers URL visited -------------------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_constructed_careers_url_is_refused() -> None:
    t, a = tenant_with_account("x1-careers", "careers.example")
    ok = careers.Visit("https://careers.example/jobs", "args-hash-1", NOW)
    for bad in (
        careers.Visit("https://careers.example/careers", " ", NOW),
        careers.Visit("ftp://careers.example/jobs", "x", NOW),
        careers.Visit("https://other.example/careers", "x", NOW),
    ):
        with pytest.raises(careers.ConstructedUrl):
            careers.check(bad, "careers.example")
    assert careers.check(
        careers.Visit("https://jobs.careers.example/", "x", NOW), "careers.example"
    )
    async with tenant_txn(t) as s:
        await careers.record_visit(s, account_id=a, domain="careers.example", visit=ok)
    assert db("SELECT careers_url, careers_fetch_ref FROM accounts WHERE id = %s", a) == [
        ("https://careers.example/jobs", "args-hash-1")
    ]
    with (
        psycopg.connect(ADMIN_DSN, autocommit=True) as c,
        pytest.raises(psycopg.errors.CheckViolation),
    ):  # the database refuses a URL without a visit
        c.execute(
            "UPDATE accounts SET careers_url = 'https://careers.example/careers',"
            " careers_fetch_ref = NULL WHERE id = %s",
            (a,),
        )


# --- DM4: anchored facts ------------------------------------------------------------------------


@pytest.mark.requires_postgres
def test_a_fact_without_evidence_or_a_hypothesis_without_question_is_refused() -> None:
    t, a = tenant_with_account("x1-facts")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute(
                "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                " fingerprint, product_grade) VALUES (%s, %s, 'k', '{}', %s, 'probable')",
                (t, a, uuid4().hex),
            )  # no evidence row at commit
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute(
                "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                " fingerprint, product_grade) VALUES (%s, %s, 'k', '{}', %s, 'hypothesis')",
                (t, a, uuid4().hex),
            )
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute(
                "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                " fingerprint, product_grade, may_state_to_prospect) VALUES (%s, %s, 'k',"
                " '{}', %s, 'probable', true)",
                (t, a, uuid4().hex),
            )


# --- DM6: bases and leads ----------------------------------------------------------------------


def test_a_count_without_its_base_cannot_render() -> None:
    assert counts.render_count(12, "matched", "job postings") == "12 matched (job postings)"
    assert counts.render_count(0, "read", "job postings") == "0 read (job postings)"
    for value, base, what in (
        (3, None, "postings"),
        (3, "seen", "postings"),
        (-1, "read", "x"),
        (3, "read", " "),
    ):
        with pytest.raises(counts.CountWithoutBase):
            counts.render_count(value, base, what)  # type: ignore[arg-type]


def test_a_date_range_alone_is_a_lead() -> None:
    cap = counts.tech_grade_cap
    assert cap("probable", has_date_range_only=True, cross_check=None) == "lead"
    assert cap("probable", has_date_range_only=True, cross_check="not_found") == "lead"
    assert cap("probable", has_date_range_only=True, cross_check="confirmed") == "probable"
    assert cap("confirmed", has_date_range_only=False, cross_check=None) == "confirmed"
    assert cap("confirmed", has_date_range_only=False, cross_check="contradicted") == "lead"
    with pytest.raises(ValueError):
        cap("A", has_date_range_only=False, cross_check=None)


# --- DM7: exports are bound to rows ------------------------------------------------------------


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode()


async def _package(t: UUID, a: UUID, store: dict[str, bytes], body: bytes) -> tuple[UUID, UUID]:
    from views.rowsources.common import ViewRow, rows_hash

    run = db(
        "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
        " reserved_credits, idempotency_key, budget_usd, finished_at) VALUES (%s, %s,"
        " 'COMPLETED', 'g', '{}', 1, %s, 5, now()) RETURNING id",
        t,
        a,
        uuid4().hex,
    )[0][0]
    cells = [
        {
            "ref": "F-0001",
            "item": "SAP: Selection",
            "finding": "Hiring a platform owner",
            "grade": "Confirmed",
            "use": "State",
            "reasoning": "S-0001",
            "lane": "sap",
        },
        {
            "ref": "F-0002",
            "item": "SAP: Executive role",
            "finding": "New CIO",
            "grade": "Probable",
            "use": "Verify",
            "reasoning": "S-0002",
            "lane": "sap",
        },
    ]
    vrows = [
        ViewRow("facts", i + 1, c["ref"], f"row-{i}", c, None, None) for i, c in enumerate(cells)
    ]
    h = rows_hash({"facts": vrows})
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for v in vrows:
            c.execute(
                "INSERT INTO view_rows (tenant_id, run_id, account_id, table_kind, position,"
                " ref, row_id, cells, source_state) VALUES (%s, %s, %s, 'facts', %s, %s, %s,"
                " %s, 'no_source')",
                (t, run, a, v.position, v.ref, v.row_id, psycopg.types.json.Jsonb(v.cells)),
            )
        c.execute(
            "INSERT INTO view_materializations (tenant_id, run_id, account_id, counts,"
            " settings_version, created_at, status, rows_hash) VALUES (%s, %s, %s, '{}',"
            " 1, now(), 'ready', %s)",
            (t, run, a, h),
        )
        key = f"tenants/{t}/exports/{run}/facts.csv"
        store[key] = body
        files = [
            {
                "kind": "facts",
                "name": "facts.csv",
                "key": key,
                "store": "objects",
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        ]
        pkg = c.execute(
            "INSERT INTO export_packages (tenant_id, run_id, account_id, kind, status,"
            " files, parity, rows_hash, requested_by, created_at) VALUES (%s, %s, %s,"
            " 'csv', 'delivered', %s, '{}', %s, 'u', now()) RETURNING id",
            (t, run, a, psycopg.types.json.Jsonb(files), h),
        ).fetchone()[0]
    return pkg, run


# the Facts by grade columns (the Summary's Platform picture by lane)
HEADER = ["Ref", "Item", "What we found", "Grade", "Use in outreach", "Reasoning and sources"]
GOOD = [
    HEADER,
    ["F-0001", "SAP: Selection", "Hiring a platform owner", "Confirmed", "State", "S-0001"],
    ["F-0002", "SAP: Executive role", "New CIO", "Probable", "Verify", "S-0002"],
]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_every_tamper_of_an_export_is_detected() -> None:
    from views.tables import specs

    headers = [c.header for c in specs()["facts"].exported_columns]
    good = [headers] + [r[: len(headers)] for r in GOOD[1:]]
    store: dict[str, bytes] = {}

    async def load(_store: str, key: str) -> bytes:
        return store[key]

    async def run_case(mutate: Any) -> export_verify.Verdict:
        t, a = tenant_with_account("x1-export")
        pkg, run = await _package(t, a, store, _csv(good))
        await mutate(t, run, pkg)
        async with tenant_txn(t) as s:
            v = await export_verify.verify(s, pkg, load, now=datetime.now(UTC))
            assert await export_verify.blocked(s, pkg) is (not v.ok)
        return v

    async def nothing(t: UUID, run: UUID, pkg: UUID) -> None:
        return None

    def file_tamper(new_rows: list[list[str]], keep_hash: bool) -> Any:
        async def go(t: UUID, run: UUID, pkg: UUID) -> None:
            key = f"tenants/{t}/exports/{run}/facts.csv"
            store[key] = _csv(new_rows)
            if keep_hash:  # the attacker also fixes up the recorded sha256
                with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
                    files = c.execute(
                        "SELECT files FROM export_packages WHERE id = %s", (pkg,)
                    ).fetchone()[0]
                    files[0]["sha256"] = hashlib.sha256(store[key]).hexdigest()
                    c.execute(
                        "UPDATE export_packages SET files = %s WHERE id = %s",
                        (psycopg.types.json.Jsonb(files), pkg),
                    )

        return go

    async def row_tamper(t: UUID, run: UUID, pkg: UUID) -> None:
        db(
            "UPDATE view_rows SET cells = jsonb_set(cells, '{finding}', '\"edited\"') WHERE"
            " run_id = %s AND position = 1",
            run,
        )

    assert (await run_case(nothing)).ok
    cell = [good[0], [good[1][0], "Hiring a CFO", *good[1][2:]], good[2]]
    dropped = good[:2]
    reordered = [good[0], good[2], good[1]]
    formula = [good[0], [good[1][0], '=HYPERLINK("x")', *good[1][2:]], good[2]]
    for name, case in (
        ("tampered cell", file_tamper(cell, False)),
        ("tampered cell, hash fixed", file_tamper(cell, True)),
        ("dropped row", file_tamper(dropped, True)),
        ("reordered rows", file_tamper(reordered, False)),
        ("formula", file_tamper(formula, True)),
        ("swapped file", file_tamper([HEADER, ["X-9999", "other account"]], False)),
        ("stored rows edited", row_tamper),
    ):
        v = await run_case(case)
        assert not v.ok and v.problems, name
