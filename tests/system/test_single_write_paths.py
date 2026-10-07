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
    "people": {"people/repo.py"},
    "person_search_pages": {"people/repo.py"},
    "person_sources": {"people/repo.py"},
    "person_profiles": {"people/repo.py"},
    "person_posts": {"people/repo.py"},
    "person_post_pulls": {"people/repo.py"},
    "person_events": {"people/repo.py"},
    "people_pipeline_runs": {"people/repo.py"},
    "run_cap_versions": {"spend/repo.py"},
    "run_cap_events": {"spend/repo.py"},
    "balance_snapshots": {"spend/repo.py"},
    "spend_reports": {"spend/repo.py"},
    "prepulled_files": {"spend/prepulled.py"},
    "spend_defects": {"connectors/ledger.py"},
    "deliverables": {"exports/store.py"},
    # parallel execution (migration 0017): unit definitions and events have one writer; the
    # governor tables are written only by the governor_* definer functions (SQL, not Python)
    "work_units": {"parallel/store.py"},
    "work_unit_events": {"parallel/store.py"},
    # credit manager v2: the limit events by the module that owns each event kind
    "spend_limit_events": {
        "spend/limits.py",  # soft_crossed
        "spend/pause.py",  # pause_opened, decisions, late_approval_refused
        "spend/exhaustion.py",  # exhausted, upload_requested
        "spend/upload.py",  # upload_accepted, upload_rejected
    },
    "notification_outbox": {"spend/notify.py"},
    "notification_deliveries": {"spend/notify.py"},
    "prepulled_intents": {"spend/exhaustion.py"},
    "prepulled_intent_events": {"spend/exhaustion.py", "spend/upload.py"},
    # model routing (migration 0018): the call ledger has one writer; candidate health is written
    # only by the llm_health_record function (called from llm/health.py)
    "llm_calls": {"llm/record.py"},
    "llm_call_attempts": {"llm/record.py"},
    "verifier_findings": {"verify/stage.py"},
    "eval_runs": {"evalgate/store.py"},
    "eval_case_results": {"evalgate/store.py"},
    "eval_metrics": {"evalgate/store.py"},
    "eval_baselines": {"evalgate/store.py"},
    # observability (migration 0019): derived rollups and insert-only observations
    "ops_cost_rollups": {"obs/costs.py"},
    "stuck_run_observations": {"obs/stuck.py"},
    "canary_probe_results": {"obs/canary.py"},
    # Phase 3 tenant plane: identity (migration 0021) and tenancy (migration 0020)
    "users": {"identity/membership.py"},
    "memberships": {"identity/membership.py"},
    "membership_events": {"identity/membership.py"},
    "auth_sessions": {"identity/sessions.py"},
    "auth_events": {"identity/sessions.py"},
    "login_states": {"identity/broker.py"},
    "saml_assertion_ids": {"identity/broker.py"},
    "idp_connections": {"identity/connections.py"},
    "tenant_events": {"tenancy/lifecycle.py"},
    "tenant_settings_versions": {"tenancy/settings.py"},
    "audit_events": {"audit/events.py"},
    "audit_chain_checks": {"audit/chain.py"},
    "view_rows": {"views/materialize.py"},
    "view_materializations": {"views/materialize.py"},
    "saved_views": {"views/saved.py"},
    "export_packages": {"views/packages.py"},
    "branding_assets": {"views/branding.py"},
    # account_runs: lifecycle (state machine, slices), guard (terminal status), connector budget
    # (spent_usd and the call count), grading (the pinned config hash)
    "account_runs": {
        "runs/lifecycle.py",
        "tracker/guard.py",
        "connectors/budget.py",
        "grading/repo.py",
        "spend/repo.py",  # the approved cap's enforcement columns (one conditional UPDATE)
        "spend/reconcile.py",  # unreconciled_usd (stale reserves, K9)
        "tenancy/settings.py",  # settings_version, set once (TN5)
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
