"""Migrations 0094 (posting fields) and 0095 (posting heuristic) on the state staging was in.

Staging ran the account-delete and account-bundle migrations as 0087 and 0088, then 0089 to 0093,
and never ran the posting migrations (they were first numbered 0087 and 0088 on another branch).
So the live schema had no ``last_seen``, ``seniority``, ``new_since_previous``,
``previous_run_id`` or ``carried`` and a ``classified_by`` check without 'heuristic'. Each test
runs inside a transaction that is rolled back, so the shared scratch schema is left as it was.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from tests.conftest import ADMIN_DSN

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
FIELDS = ("last_seen", "seniority", "new_since_previous", "previous_run_id", "carried")


def _sql(name: str) -> str:
    return (MIGRATIONS / name).read_text()


def _fields(c: psycopg.Connection) -> set[str]:
    rows = c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'"
        " AND table_name IN ('job_postings', 'job_pipeline_runs') AND column_name = ANY(%s)",
        (list(FIELDS),),
    ).fetchall()
    return {r[0] for r in rows}


def _check(c: psycopg.Connection) -> str:
    row = c.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'ck_job_posting_classifications_by'"
    ).fetchone()
    assert row is not None
    return str(row[0])


def _staging_state(c: psycopg.Connection) -> None:
    """The schema as it stood at revision 0093 on staging: the two migrations' own down scripts
    take it back (rows the heuristic classed are relabelled first, as they must be)."""
    c.execute(_sql("0095_posting_heuristic.down.sql"))
    c.execute(_sql("0094_posting_fields.down.sql"))
    assert _fields(c) == set()
    assert "heuristic" not in _check(c)


def test_the_posting_migrations_apply_to_the_schema_staging_was_in() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        _staging_state(c)
        c.execute(_sql("0094_posting_fields.sql"))
        c.execute(_sql("0095_posting_heuristic.sql"))
        assert _fields(c) == set(FIELDS)
        assert "heuristic" in _check(c)
        carried = c.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_job_pipeline_runs_carried'"
        ).fetchone()
        assert carried is not None and "carried >= 0" in carried[0]
        c.rollback()


def test_the_posting_migrations_apply_twice_and_over_a_partly_migrated_schema() -> None:
    """Idempotent: a second run, and a schema that holds only some of the pieces, change
    nothing and fail nowhere."""
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute(_sql("0094_posting_fields.sql"))
        c.execute(_sql("0095_posting_heuristic.sql"))
        before = (_fields(c), _check(c))
        c.execute(_sql("0094_posting_fields.sql"))
        c.execute(_sql("0095_posting_heuristic.sql"))
        assert (_fields(c), _check(c)) == before
        # only part of 0094 present
        c.execute(
            "ALTER TABLE job_pipeline_runs DROP CONSTRAINT ck_job_pipeline_runs_carried;"
            " ALTER TABLE job_pipeline_runs DROP COLUMN previous_run_id;"
            " ALTER TABLE job_postings DROP COLUMN seniority"
        )
        c.execute(_sql("0094_posting_fields.sql"))
        assert _fields(c) == set(FIELDS)
        c.rollback()


def test_the_posting_migrations_go_down_and_up_again() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute(_sql("0095_posting_heuristic.down.sql"))
        assert "heuristic" not in _check(c)
        c.execute(_sql("0094_posting_fields.down.sql"))
        assert _fields(c) == set()
        c.execute(_sql("0094_posting_fields.sql"))
        c.execute(_sql("0095_posting_heuristic.sql"))
        assert _fields(c) == set(FIELDS)
        assert "heuristic" in _check(c)
        c.rollback()


def test_the_chain_is_linear_and_the_two_live_migrations_keep_their_numbers() -> None:
    """0087 (account delete) and 0088 (account bundles) are live on staging and must keep their
    names and revisions; the posting migrations follow the newest one that ran there (0093)."""
    versions = MIGRATIONS / "versions"
    chain: dict[str, str | None] = {}
    for path in sorted(versions.glob("0*.py")):
        text = path.read_text()
        rev = next(ln for ln in text.splitlines() if ln.startswith("revision = "))
        down = next(ln for ln in text.splitlines() if ln.startswith("down_revision = "))
        chain[rev.split('"')[1]] = None if "None" in down else down.split('"')[1]
    assert chain["0087"] == "0086" and (versions / "0087_account_delete.py").exists()
    assert chain["0088"] == "0087" and (versions / "0088_account_bundles.py").exists()
    assert chain["0089"] == "0088"
    assert chain["0094"] == "0093" and (versions / "0094_posting_fields.py").exists()
    assert chain["0095"] == "0094" and (versions / "0095_posting_heuristic.py").exists()
    # every revision has exactly one child but the head: no branch
    children = [d for d in chain.values() if d is not None]
    assert len(children) == len(set(children))
    assert set(chain) - set(children) == {"0095"}
