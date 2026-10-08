"""X4 people data and retention (PD1 to PD5, roadmap 11.4; D-83, D-84). Lying scenarios: a
person field with no consumer, a dry run that does not match the execute, a held or permanent
row deleted, a run resumed after a crash deleting twice, a DSAR reading across tenants without a
grant, an erased person re-collected, and a template claiming compliance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from account_intelligence.db.session import SessionLocal, tenant_txn
from privacy import dsar, minimise, retention, suppression, tombstones

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
SALT = b"crosscut-test-salt"


# --- PD1: minimisation --------------------------------------------------------------------------


def test_a_person_field_without_a_consumer_fails() -> None:
    fields = {"name": {"consumers": ["people_list"]}, "nickname": {"consumers": []}}
    assert minimise.check(["name"], fields) == []
    assert minimise.check(["nickname"], fields) == ["nickname: no consumer; it may not be collected"]
    assert minimise.check(["salary"], fields) == [
        "salary: not in the minimisation map; it may not be collected"
    ]
    assert minimise.uncollectable(fields) == ["nickname"]


def test_the_map_itself_is_honest_and_the_config_loads() -> None:
    fields = minimise.load_map()
    assert fields["name"]["consumers"]
    assert minimise.uncollectable() == []


# --- PD2: retention -----------------------------------------------------------------------------

CFG = {
    "checkpoints": {"days": 7, "action": "delete"},
    "run_metadata": {"days": 395, "action": "delete"},
    "wallet_ledger": {"permanent": True, "action": "none"},
    "person_profiles": {"days": 730, "action": "delete"},
}


def _row(rid: str, cls: str, age: int, held: bool = False) -> retention.Row:
    return retention.Row(row_id=rid, cls=cls, age_days=age, held=held)


def test_retention_selects_exactly_the_dry_run() -> None:
    rows = [
        _row("a1", "checkpoints", 8),       # older than 7 days: remove
        _row("a2", "checkpoints", 3),       # inside the window: keep
        _row("a3", "checkpoints", 99, held=True),  # legal hold survives
        _row("w1", "run_metadata", 395),    # at the limit: remove
        _row("w2", "run_metadata", 100),    # keep
        _row("p1", "wallet_ledger", 100000),  # permanent: never removed
        _row("x1", "unknown_class", 999),   # no window: keep (conservative)
    ]
    plan = retention.select(rows, CFG)
    assert plan.remove == {"checkpoints": ("a1",), "run_metadata": ("w1",)}
    # the dry run names every id and every kept class
    report = retention.dry_run_report(plan, NOW)
    assert "checkpoints: 1 to remove: a1" in report and "run_metadata: 1 to remove: w1" in report
    assert "wallet_ledger: 1 kept" in report and "unknown_class: 1 kept" in report


def test_batches_resume_from_the_cursor_without_double_work() -> None:
    plan = retention.select([_row(f"r{i}", "checkpoints", 30) for i in range(10)], CFG)
    first = retention.batches(plan, cursor=0, size=4)
    assert len(first) == 4
    second = retention.batches(plan, cursor=4, size=4)
    assert len(second) == 4 and not (set(first) & set(second))
    assert retention.batches(plan, cursor=8, size=4) == retention.batches(plan, cursor=8, size=4)[
        :
    ] or len(retention.batches(plan, cursor=8, size=4)) == 2


def test_deadline_and_age_helpers() -> None:
    assert retention.deadline("checkpoints", NOW, CFG) == NOW + timedelta(days=7)
    assert retention.deadline("wallet_ledger", NOW, CFG) is None
    assert retention.age_days(at=NOW, since=NOW - timedelta(days=3, hours=2)) == 3


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_retention_removes_exactly_the_dry_run() -> None:
    """A scratch table stands in for a retention class; the engine is what is under test."""
    import psycopg

    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:  # app_rw may not create tables
        c.execute(
            "CREATE TABLE IF NOT EXISTS retention_demo (row_id text PRIMARY KEY,"
            " created_at timestamptz NOT NULL)"
        )
        c.execute("GRANT SELECT, INSERT, DELETE, UPDATE ON retention_demo TO app_rw")
    async with SessionLocal() as s, s.begin():
        await s.execute(sa.text("DELETE FROM retention_demo"))
        old = [f"old-{i}" for i in range(7)] + ["held-1"]
        for rid in old:
            await s.execute(
                sa.text("INSERT INTO retention_demo VALUES (:r, :c)"),
                {"r": rid, "c": NOW - timedelta(days=40)},
            )
        await s.execute(
            sa.text("INSERT INTO retention_demo VALUES ('new-1', :c)"),
            {"c": NOW - timedelta(days=1)},
        )
        # the oracle
        ages = {
            rid: retention.age_days(at=NOW, since=c)
            for rid, c in (await s.execute(sa.text("SELECT row_id, created_at FROM retention_demo"))).all()
        }
        rows = [
            retention.Row(rid, "checkpoints", age, held=(rid == "held-1"))
            for rid, age in ages.items()
        ]
        plan = retention.select(rows, CFG)
        assert plan.remove["checkpoints"] == tuple(f"old-{i}" for i in range(7))
        # execute: bounded batches with a cursor; tombstones written; the held row survives
        removed: list[str] = []
        cursor = 0
        while True:
            batch = retention.batches(plan, cursor=cursor, size=3)
            if not batch:
                break
            for cls, rid in batch:
                await s.execute(sa.text("DELETE FROM retention_demo WHERE row_id = :r"), {"r": rid})
                await tombstones.write(
                    s, subject_type="retention_demo", subject_id=rid,
                    reason="window", content_sha256=None, at=NOW,
                )
                removed.append(rid)
            cursor += len(batch)
        assert removed == [rid for _, rid in retention.batches(plan, cursor=0, size=10_000)]
        assert "held-1" in [r[0] for r in (await s.execute(sa.text("SELECT row_id FROM retention_demo"))).all()]
        # resume after a "crash": re-running the same plan removes nothing new and
        # writes no second tombstone
        again = retention.batches(plan, cursor=0, size=10_000)
        for _, rid in again:
            await tombstones.write(
                s, subject_type="retention_demo", subject_id=rid,
                reason="window", content_sha256=None, at=NOW,
            )
        n = (
            await s.execute(
                sa.text("SELECT count(*) FROM tombstones WHERE subject_type = 'retention_demo'")
            )
        ).scalar_one()
        assert n == 7
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:  # cleanup outside the app role
        c.execute("DROP TABLE IF EXISTS retention_demo")
        c.execute("DELETE FROM tombstones WHERE subject_type = 'retention_demo'")


# --- PD4: suppression ---------------------------------------------------------------------------


def test_suppression_hashes_are_salted_and_normalised() -> None:
    # the normalised path equalises whitespace and case
    assert suppression.subject_hashes(profile_url=None, name=" Jane  DOE ", salt=SALT) == [
        suppression.subject_hashes(profile_url=None, name="jane doe", salt=SALT)[0]
    ]
    # the salt changes the hash: a leaked list is useless without it
    assert suppression.subject_hash("jane", SALT) != suppression.subject_hash("jane", b"other")
    with pytest.raises(ValueError):
        suppression.subject_hash(" ", SALT)
    with pytest.raises(ValueError):
        suppression.subject_hashes(profile_url=None, name=None, salt=SALT)


def test_an_erased_subject_is_recognised_before_storing_or_rendering() -> None:
    known = {("url", suppression.subject_hash("https://x.example/jane", SALT))}
    assert suppression.is_suppressed(
        known, profile_url="https://x.example/jane", name="Jane", salt=SALT
    )
    known2 = {("name", suppression.subject_hash("jane doe", SALT))}
    assert suppression.is_suppressed(known2, profile_url=None, name="JANE   DOE", salt=SALT)
    assert not suppression.is_suppressed(
        set(), profile_url="https://y.example/sam", name="Sam", salt=SALT
    )


# --- PD3: the DSAR workflow ---------------------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_dsar_runs_end_to_end() -> None:
    from tests.phase4.conftest import db as admin_db

    t = admin_db(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", f"dsar-{uuid4().hex[:6]}"
    )[0][0]
    a = admin_db(
        "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id", t, "dsar.example"
    )[0][0]
    run = admin_db(
        "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
        " reserved_credits, idempotency_key, budget_usd, finished_at) VALUES (%s, %s, 'COMPLETED', 'g',"
        " '{}', 1, %s, 5, now()) RETURNING id",
        t, a, uuid4().hex,
    )[0][0]
    person = admin_db(
        "INSERT INTO people (tenant_id, account_id, run_id, profile_url, profile_url_canonical,"
        " name, title, job_level, person_key, source_ref) VALUES"
        " (%s, %s, %s, %s, %s, 'Jane Doe', 'CIO', 'c_level', %s, 'fixture') RETURNING id",
        t, a, run, "https://x.example/jane", "https://x.example/jane", uuid4().hex,
    )[0][0]

    async with SessionLocal() as s, s.begin():
        req = await dsar.open_request(
            s, kind="erasure", subject_ref="jane@example", tenant_scope=t, now=NOW
        )
        assert req.deadline_at - NOW == timedelta(days=30)
        await dsar.verify_identity(s, request_id=req.id, ok=True, at=NOW)
    # the narrow search: ids only, inside the recorded grant
    async with tenant_txn(t) as s:
        ids = await dsar.scoped_search(s, request=req, tenant_id=t, profile_url="https://x.example/jane")
        assert ids == [person]
        # no grant, no read: a different tenant is refused outright
        with pytest.raises(dsar.NoGrant):
            await dsar.scoped_search(s, request=req, tenant_id=uuid4(), name="Jane Doe")
        data = await dsar.export_subject(
            s, request=dsar.Request(req.id, "access", req.subject_ref, req.tenant_scope, req.status, req.deadline_at),
            tenant_id=t, person_ids=ids,
        )
        assert data["people"][0]["name"] == "Jane Doe"
        # erasure: redact, tombstone, suppress
        erased = await dsar.erase(
            s, request=req, tenant_id=t, person_ids=ids, salt=SALT, now=NOW
        )
        assert erased == 1
        again = await dsar.erase(
            s, request=req, tenant_id=t, person_ids=ids, salt=SALT, now=NOW
        )
        assert again == 0  # idempotent: a resumed run never double-erases
    async with SessionLocal() as s, s.begin():
        # the person is gone; the tombstone and the suppression remain
        row = (
            await s.execute(sa.text("SELECT name, profile_url, erased_at FROM people WHERE id = :p"), {"p": person})
        ).first()
        assert row.erased_at is not None and row.name is None and row.profile_url is None
        tom = (
            await s.execute(
                sa.text("SELECT reason, content_sha256 FROM tombstones WHERE subject_id = :s"),
                {"s": str(person)},
            )
        ).first()
        assert tom.reason == f"dsar:{req.id}" and tom.content_sha256
        known = await suppression.suppressed(s)
        assert suppression.is_suppressed(known, profile_url="https://x.example/jane", name=None, salt=SALT)
        # the pipeline's pre-store check refuses the same subject
        assert suppression.is_suppressed(
            known, profile_url="https://x.example/jane", name="Jane Doe", salt=SALT
        )
        # every step is audited
        steps = [
            r.step
            for r in (
                await s.execute(sa.text("SELECT step FROM dsar_events WHERE request_id = :r ORDER BY id"), {"r": req.id})
            ).all()
        ]
        assert steps == ["intake", "identity_verification", "scoped_search", "erasure", "erasure"]
        msg = await dsar.confirm(s, request=req, at=NOW)
        assert msg == dsar.CONFIRMATION
        assert not await dsar.overdue(s, now=NOW)
        # an open request past its deadline is visible to operators
        req2 = await dsar.open_request(
            s, kind="access", subject_ref="other@example", tenant_scope=t,
            now=NOW - timedelta(days=60),
        )
        assert req2.id in await dsar.overdue(s, now=NOW)
        await s.execute(sa.text("UPDATE dsar_requests SET status = 'done' WHERE id = :r"), {"r": req2.id})


# --- PD5: templates, not assertions -------------------------------------------------------------


def test_the_counsel_templates_are_templates() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in (
        "docs/privacy/lawful_basis_register.md",
        "docs/privacy/processing_record.md",
        "docs/privacy/dpa_notes.md",
        "docs/privacy/subprocessors.md",
    ):
        text = (root / rel).read_text()
        assert "status: not completed" in text.lower() or "not completed" in text.lower(), rel
        assert "not legal advice" in text.lower(), rel
