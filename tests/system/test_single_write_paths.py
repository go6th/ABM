"""W1: every table has one write path. A grep over ``src`` for INSERT, UPDATE and DELETE pins each
table (and each group of ``account_runs`` columns) to the module that owns it; import-linter
(``lint-imports``, contracts in pyproject.toml) keeps lower layers from importing higher ones.
GUARANTEE(test_each_table_is_written_only_by_its_owner)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITE = re.compile(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)")  # SQL is upper case here

OWNERS: dict[str, set[str]] = {
    "coverage_rows": {"tracker/repo.py"},
    "coverage_row_events": {"tracker/repo.py"},
    "run_coverage_meta": {"tracker/repo.py"},
    "facts": {"grading/repo.py"},
    "fact_evidence": {"grading/repo.py"},
    "grade_events": {"grading/repo.py"},
    "claim_drops": {"grading/repo.py"},
    "lead_verifications": {"grading/repo.py"},
    "lead_verification_queries": {"grading/repo.py"},
    "tool_calls": {"connectors/ledger.py"},
    "tool_call_events": {"connectors/ledger.py"},
    "run_leases": {"connectors/ledger.py"},
    "wallets": {"runs/lifecycle.py"},
    "wallet_ledger": {"runs/lifecycle.py"},
    "runs": {"runs/lifecycle.py", "tracker/guard.py"},
    "accounts": {"runs/lifecycle.py", "workflow/stages/core.py"},
    "job_postings": {"jobs/repo.py"},
    "job_posting_platform_sentences": {"jobs/repo.py"},
    "job_posting_classifications": {"jobs/repo.py"},
    "job_posting_observations": {"jobs/repo.py"},
    "job_pipeline_runs": {"jobs/repo.py"},
    "job_pipeline_passes": {"jobs/repo.py"},
    # account_runs: lifecycle (state machine, slices), guard (terminal status), connector budget
    # (spent_usd and the call count), grading (the pinned config hash)
    "account_runs": {
        "runs/lifecycle.py",
        "tracker/guard.py",
        "connectors/budget.py",
        "grading/repo.py",
    },
}
IGNORED = {"statements", "or"}  # words matched in comments and docstrings, not tables


def test_each_table_is_written_only_by_its_owner() -> None:
    found: dict[str, set[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        rel = str(path.relative_to(ROOT / "src")).replace("account_intelligence/", "")
        for match in WRITE.finditer(path.read_text()):
            table = match.group(2).lower()
            if table in IGNORED:
                continue
            found.setdefault(table, set()).add(rel)
    unknown = sorted(t for t in found if t not in OWNERS)
    assert not unknown, f"writes to tables with no declared owner: {unknown}"
    for table, files in found.items():
        assert files <= OWNERS[table], f"{table} written by {sorted(files - OWNERS[table])}"


def test_import_contracts_hold() -> None:
    result = subprocess.run(
        [str(Path(sys.executable).with_name("lint-imports"))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-2000:]
