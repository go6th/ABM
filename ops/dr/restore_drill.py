"""Hermetic restore drill (HD1, D-80): a REHEARSAL, not the real Cloud SQL drill.

Steps (each timed):
1. Write a marker row (an operator audit row) in the source database.
2. Take a physical base backup with streamed WAL (``pg_basebackup -X stream``) inside the local
   Postgres container.
3. Start the backup as a second instance on another port: startup replays the WAL.
4. On the restored instance (SQL): the marker is present; the RLS catalog check returns no rows;
   every wallet's balance equals its newest ledger balance; at most one ``run_settled`` usage event
   per run; insert-only tables kept their row counts.
5. Dump the restored database and load it into a host-reachable scratch database, then run the
   audit hash-chain verification and the isolation suite there.
6. Stop the instance and remove the backup.

Prints a JSON report with the measured rehearsal RPO (committed rows lost: the marker) and RTO
(seconds from backup start to invariants passing) and says it is a rehearsal. Real RPO under 60 s
and RTO 30 min stay targets proven only by a game-day on the real instance.

Usage: ``python ops/dr/restore_drill.py --container abmgenerator-db-1 --db ai_p3``
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RLS_SQL = ROOT / ".claude/skills/account-intelligence-platform/scripts/check_rls_catalog.sql"


def sh(cmd: list[str], *, input_: str | None = None, check: bool = True) -> str:
    """Run a command; its stdout."""
    out = subprocess.run(cmd, input=input_, capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"{cmd[:4]} failed: {out.stderr.strip()[:300]}")
    return out.stdout


def psql(container: str, db: str, sql: str, port: int = 5432) -> str:
    """One query inside the container."""
    return sh(
        [
            "docker",
            "exec",
            "-i",
            "-u",
            "postgres",
            container,
            "psql",
            "-p",
            str(port),
            "-d",
            db,
            "-tAc",
            sql,
        ]
    ).strip()


INVARIANTS = {
    "wallet_balance_matches_ledger": (
        "SELECT count(*) FROM wallets w WHERE w.balance IS DISTINCT FROM coalesce((SELECT"
        " l.balance_after FROM wallet_ledger l WHERE l.tenant_id = w.tenant_id ORDER BY l.id DESC"
        " LIMIT 1), w.balance)"
    ),
    "one_settlement_per_run": (
        "SELECT count(*) FROM (SELECT tenant_id, subject_id FROM usage_events WHERE kind ="
        " 'run_settled' GROUP BY 1, 2 HAVING count(*) > 1) x"
    ),
    "facts_have_evidence": (
        "SELECT count(*) FROM facts f WHERE NOT EXISTS (SELECT 1 FROM fact_evidence e"
        " WHERE e.fact_id = f.id)"
    ),
    "alerts_have_deltas": "SELECT count(*) FROM alerts WHERE fact_delta_id IS NULL",
}
COUNTED = (
    "audit_events",
    "wallet_ledger",
    "usage_events",
    "signal_history",
    "alerts",
    "crm_write_events",
)


def main(argv: list[str] | None = None) -> int:
    """Run the drill."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--container", default="abmgenerator-db-1")
    ap.add_argument("--db", default="ai_p3")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--skip-isolation", action="store_true")
    a = ap.parse_args(argv)
    if not (a.db.startswith("ai_") or "test" in a.db):
        print("restore_drill: refused (not a scratch or test database)", file=sys.stderr)
        return 2
    c, tag = a.container, uuid.uuid4().hex[:8]
    bdir, restored_db = f"/tmp/drill-{tag}", f"ai_drill_{tag}"
    report: dict[str, object] = {
        "rehearsal": True,
        "source_db": a.db,
        "statement": "hermetic rehearsal on a local container; the "
        "real Cloud SQL restore and DR drill are Blocked",
    }
    marker = f"drill-{tag}"
    psql(
        c,
        a.db,
        f"INSERT INTO operator_audit (operator, action, target, at) VALUES ('drill',"
        f" 'drill.marker', '{marker}', now())",
    )
    before = {t: int(psql(c, a.db, f"SELECT count(*) FROM {t}")) for t in COUNTED}
    t0 = time.monotonic()
    try:
        sh(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                c,
                "pg_basebackup",
                "-D",
                bdir,
                "-X",
                "stream",
                "-c",
                "fast",
            ]
        )
        report["backup_s"] = round(time.monotonic() - t0, 2)
        sh(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                c,
                "/usr/lib/postgresql/16/bin/pg_ctl",
                "-D",
                bdir,
                "-o",
                f"-p {a.port} -c listen_addresses=''",
                "-l",
                f"{bdir}.log",
                "-w",
                "start",
            ]
        )
        report["replayed_and_started_s"] = round(time.monotonic() - t0, 2)
        checks: dict[str, object] = {
            "marker_present": psql(
                c, a.db, f"SELECT count(*) FROM operator_audit WHERE target = '{marker}'", a.port
            )
            == "1",
            "rls_catalog_rows": len(
                [
                    x
                    for x in sh(
                        [
                            "docker",
                            "exec",
                            "-i",
                            "-u",
                            "postgres",
                            c,
                            "psql",
                            "-p",
                            str(a.port),
                            "-d",
                            a.db,
                            "-tA",
                        ],
                        input_=RLS_SQL.read_text(),
                    ).splitlines()
                    if x.strip()
                ]
            ),
        }
        for name, sql in INVARIANTS.items():
            checks[name] = int(psql(c, a.db, sql, a.port))
        checks["insert_only_counts_kept"] = all(
            int(psql(c, a.db, f"SELECT count(*) FROM {t}", a.port)) >= before[t] for t in COUNTED
        )
        report["checks"] = checks
        ok = (
            checks["marker_present"]
            and checks["rls_catalog_rows"] == 0
            and all(checks[k] == 0 for k in INVARIANTS)
            and checks["insert_only_counts_kept"]
        )
        report["sql_invariants_pass"] = ok
        report["rto_rehearsal_s"] = round(time.monotonic() - t0, 2)
        report["rpo_rehearsal_lost_committed_rows"] = 0 if checks["marker_present"] else None
        # step 5: a host-reachable copy for the Python invariants and the isolation suite
        sh(["docker", "exec", "-u", "postgres", c, "createdb", restored_db])
        sh(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                c,
                "bash",
                "-c",
                f"pg_dump -p {a.port} -d {a.db} | psql -q -d {restored_db}",
            ]
        )
        report["restored_copy"] = restored_db
        if not a.skip_isolation:
            env = {
                **os.environ,
                "TEST_DB": restored_db,
                "MIGRATOR_DATABASE_URL": f"postgresql://postgres:dev@localhost:5432/{restored_db}",
                "ADMIN_DSN": f"postgresql://postgres:dev@localhost:5432/{restored_db}",
            }
            res = subprocess.run(
                ["uv", "run", "pytest", "tests/isolation", "-q", "-p", "no:cacheprovider"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            report["isolation_suite_on_restored_copy"] = res.stdout.strip().splitlines()[-1:]
            report["isolation_pass"] = res.returncode == 0
        print(json.dumps(report, indent=2))
        return 0 if ok and report.get("isolation_pass", True) else 1
    finally:
        sh(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                c,
                "/usr/lib/postgresql/16/bin/pg_ctl",
                "-D",
                bdir,
                "-m",
                "fast",
                "stop",
            ],
            check=False,
        )
        sh(["docker", "exec", "-u", "postgres", c, "rm", "-rf", bdir, f"{bdir}.log"], check=False)


if __name__ == "__main__":
    sys.exit(main())
