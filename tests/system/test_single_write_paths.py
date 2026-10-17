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
    "sources": {"account_intelligence/db/source_ids.py"},
    "claim_drops": {"grading/repo.py"},
    "lead_verifications": {"grading/repo.py"},
    "lead_verification_queries": {"grading/repo.py"},
    "tool_calls": {"connectors/ledger.py"},
    "tool_call_events": {"connectors/ledger.py"},
    "run_leases": {"connectors/ledger.py"},
    "wallets": {"runs/lifecycle.py"},
    "wallet_ledger": {"runs/lifecycle.py"},
    "runs": {"runs/lifecycle.py", "tracker/guard.py"},
    "accounts": {
        "runs/lifecycle.py",
        "datamodel/careers.py",
        "replica/schedules.py",  # refresh_cadence and next_refresh_at only
    },
    "job_postings": {"jobs/repo.py"},
    "job_posting_platform_sentences": {"jobs/repo.py"},
    "job_posting_classifications": {"jobs/repo.py"},
    "job_posting_observations": {"jobs/repo.py"},
    "job_pipeline_runs": {"jobs/repo.py"},
    "job_pipeline_passes": {"jobs/repo.py"},
    "people": {"people/repo.py", "privacy/dsar.py"},
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
    "deliverables": {
        "exports/store.py",
        "replica/library.py",  # the files of a 6th Library copy
    },
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
    # research cache (migration 0049): the model reply log has one writer
    "model_reply_log": {"llm/reply_cache.py"},
    # research files (migration 0051): the earlier upload path is gone and nothing writes its
    # tables any more, except the file model call log, where documents record embedding calls
    "research_file_model_calls": {"documents/repo.py"},
    # two research input modes (migration 0052): the document store and the private index, the
    # gate's insert-only records, and the facts marked when their document is deleted
    "documents": {"documents/repo.py"},
    "document_passages": {"documents/repo.py"},
    "document_parts": {"documents/repo.py"},
    "document_tables": {"documents/repo.py"},
    "document_table_rows": {"documents/repo.py"},
    "raw_files": {"documents/repo.py"},
    "download_jobs": {"replica/downloads.py"},
    "document_redactions": {"documents/repo.py"},
    "document_policies": {"documents/repo.py"},
    "document_deletions": {"documents/repo.py"},
    "run_documents": {"documents/repo.py"},
    "chunks": {"documents/repo.py"},
    "run_gate_results": {"documents/repo.py"},
    "leak_checks": {"documents/repo.py"},
    "internal_deliverables": {"documents/repo.py"},
    "fact_reevaluations": {"grading/repo.py"},
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
    "memberships": {
        "identity/membership.py",
        "replica/settings.py",  # last_active_at only (idle expiry counts from it)
    },
    "membership_events": {"identity/membership.py"},
    "auth_sessions": {"identity/sessions.py"},
    "auth_events": {"identity/sessions.py"},
    "login_states": {"identity/broker.py"},
    "saml_assertion_ids": {"identity/broker.py"},
    "idp_connections": {"identity/connections.py"},
    "tenant_events": {"tenancy/lifecycle.py"},
    "tenant_settings_versions": {"tenancy/settings.py"},
    "audit_events": {"audit/events.py"},
    "chat_threads": {"chat/store.py", "api/routes/ask.py"},
    "user_preferences": {"replica/models.py"},
    "user_limits": {"replica/limits.py"},
    "usage_periods": {"replica/limits.py"},
    "limit_requests": {"replica/limits.py"},
    "chat_turns": {"chat/store.py"},
    "question_categories": {"chat/store.py"},
    "tenant_model_credentials": {"chat/credentials.py"},
    "audit_chain_checks": {"audit/chain.py"},
    "view_rows": {"views/materialize.py"},
    "view_materializations": {"views/materialize.py"},
    "saved_views": {"views/saved.py"},
    "export_packages": {"views/packages.py"},
    "branding_assets": {"views/branding.py"},
    # consent and status by oauth; the fetch cursor and last fetch time by the framework
    "connector_connections": {"connectors_client/oauth.py", "connectors_client/framework.py"},
    "connector_oauth_states": {"connectors_client/oauth.py"},
    "connector_grants": {"connectors_client/framework.py"},
    "connector_items": {"connectors_client/framework.py"},
    "connector_quarantine": {"connectors_client/quarantine.py"},
    "connector_access_log": {"connectors_client/access_log.py"},
    "usage_events": {"metering/events.py"},
    "plan_assignments": {"admin/quotas.py"},
    "quota_versions": {"admin/quotas.py"},
    "support_grants": {"admin/support.py"},
    "support_session_events": {"admin/support.py"},
    "feature_flags": {"admin/flags.py"},
    "flag_events": {"admin/flags.py"},
    "broker_keys": {"admin/keys.py"},
    "operator_audit": {"admin/tenants.py"},
    "run_feedback": {"pilot/feedback.py"},
    "usefulness_events": {"pilot/usefulness.py"},
    "refresh_slots": {"refresh/slots.py"},
    "refresh_runs": {"refresh/records.py"},
    "refresh_row_plans": {"refresh/records.py"},
    "deltas": {"refresh/records.py"},
    "delta_items": {"refresh/records.py"},
    "signal_history": {"refresh/history.py"},
    "missing_observations": {"refresh/observations.py"},
    "monitor_configs": {"signals/intake.py"},
    "monitor_runs": {"signals/intake.py"},
    "webhook_receipts": {"signals/intake.py"},
    "intake_drops": {"signals/intake.py"},
    "signal_events": {"signals/events.py"},
    "signal_event_evidence": {"signals/events.py"},
    "signal_event_links": {"signals/events.py"},
    "alerts": {"alerts/engine.py"},
    "alert_outbox": {"alerts/engine.py", "alerts/outbox.py", "alerts/digest.py"},
    "alert_suppressions": {"alerts/engine.py"},
    "subscriptions": {"alerts/subscriptions.py"},
    "alert_feedback": {"alerts/feedback.py"},
    "digest_runs": {"alerts/digest.py"},
    "analyst_label_tasks": {"quality/analyst_loop.py"},
    "analyst_labels": {"quality/analyst_loop.py"},
    "release_checks": {"quality/release_check.py"},
    "contract_fingerprints": {"contracts/monitor.py"},
    "contract_operation_status": {"contracts/monitor.py"},
    "contract_drift_events": {"contracts/monitor.py"},
    "dsar_requests": {"privacy/dsar.py"},
    "dsar_events": {"privacy/dsar.py"},
    "suppression_entries": {"privacy/suppression.py"},
    "tombstones": {"privacy/tombstones.py"},
    "monitoring_budget_versions": {"governance/monitoring_budget.py"},
    "monitoring_usage": {"governance/monitoring_budget.py"},
    "throttle_events": {"governance/monitoring_budget.py"},
    "crm_connection_events": {"crm_writeback/service.py"},
    "crm_mappings": {"crm_writeback/service.py"},
    "crm_writes": {"crm_writeback/service.py"},
    "crm_write_events": {"crm_writeback/service.py"},
    "export_verifications": {"datamodel/export_verify.py"},
    "usage_counters": {"metering/limits.py"},
    "usage_thresholds": {"metering/limits.py"},
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
        "agents/adapters.py",  # run_mode, parent_run_id, skill config pin, set at creation (D-102)
    },
    # ABM agent layer (migration 0041): insert-only manifests, consents and sense checks
    "pull_manifests": {"agents/adapters.py"},
    "people_data_consents": {"agents/adapters.py"},
    "abm_sense_checks": {"agents/adapters.py"},
    "abm_distill_outputs": {"agents/production/state.py"},
    # replica (migrations 0053 to 0056): client limits, the credit pool and its jobs, notices
    "client_settings": {"replica/settings.py"},
    "platform_defaults": {"replica/settings.py"},
    "isolation_checks": {"replica/isolation.py"},
    "isolation_check_pair": {"replica/isolation.py"},
    "credit_entries": {"replica/credits.py"},
    "research_jobs": {"replica/credits.py"},
    "notifications": {"replica/notifications.py"},
    "library_uploads": {"replica/library.py"},
    "library_accounts": {"replica/library.py"},
    "library_publications": {"replica/library.py"},
    "report_sections": {
        "agents/production/state.py",
        "replica/library.py",  # the narrative of a 6th Library copy
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
