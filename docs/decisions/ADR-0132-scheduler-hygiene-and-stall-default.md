# ADR-0132: dead scheduler entries removed; the sweeper's jobs documented; stall_after_s 1800

- Status: Accepted (code); Terraform planned, NOT applied
- Date: 2026-11-03
- Deciders: owner (fix prompt E, task 5)

## Context

`infra/stack/main.tf` created two paused Cloud Scheduler HTTP ticks whose routes were never
built: `retention` (`/tasks/retention`) and `alert-digest` (`/tasks/alert-digest`).
`api/routes/tasks.py` has run-slice, document-process, approval-timeout, research-job,
download-everything, schedules-tick, maildrive-sync, access-expiry, sweep, audit-verify,
notify-relay, metrics and isolation-check, nothing else. The sweeper's docs named three jobs while
`api.main.sweep_pass` runs six. `platform_defaults.stall_after_s` had only the migration 0067
column default (1800) and the code fallback `spend_guard.DEFAULTS`.

## Decision

1. **Removed** both entries from `module "scheduler"` `http_ticks` (plan: two
   `google_cloud_scheduler_job` destroys). Recorded here and in the Terraform comment and the
   runbook: **the retention sweeper is not running, so data retention is not enforced in
   staging** (no scheduled job deletes or expires tenant data by age; account deletion and the
   purge job are the only deletion paths). A retention route and its tick come back together
   once the owner confirms the deletion policy. No alert digest is sent either.
2. **The sweeper's jobs**: `runs/sweeper.py`'s module docstring lists the six jobs of
   `sweep_pass` in order (`_sweep_documents`, `sweep_once`, `_mark_stalled`, `_auto_resume`,
   `_close_ended_jobs`, `_recover_handoffs`); `tests/runs/test_sweeper_jobs.py` parses that list
   and the `try` blocks of `sweep_pass` and fails when the count or the order differs.
3. **`stall_after_s` = 1800 s (30 minutes), explicit**: `spend_guard.STALL_AFTER_S_DEFAULT`
   (used by `DEFAULTS`), equal to migration 0067's column default. Kept at 1800: one slice is
   1650 s (`config/workflow_v1.yaml`), the ledger and every model call move `progress_at`, and a
   long model or batch wait beats the heartbeat every 300 s (`platform.ports` `heartbeat_seconds`),
   so a live run does not read stalled; no evidence found for another value. The operator
   Defaults page shows it beside the editable value ("minutes (default 30 minutes)") from the new
   `ConsoleDefaults.stall_after_s_default` field (contract, OpenAPI and web client regenerated;
   vitest `defaults-tick-limits.test.tsx`).
