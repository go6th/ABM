# Runbook: a run stuck waiting for its credit cap approval

Symptom: `account_runs.status = 'AWAITING_APPROVAL'` and `cap_version IS NULL`.

1. Read the proposal: `SELECT version, cap_usd, proposal_hash, created_at FROM run_cap_versions
   WHERE run_id = :r ORDER BY version` and the events `SELECT event, principal, reason, at FROM
   run_cap_events WHERE run_id = :r ORDER BY id`.
2. If the latest version is `proposed` and within `approval_ttl_minutes`, an approver of the tenant
   approves it through the API with that version and hash (`POST /v1/runs/{id}/cap/approve`), then
   approves the run (`POST /v1/runs/{id}/approve`). Never write the cap columns by hand: the nightly
   invariant flags a cap that does not match an approved version.
3. If the approval is refused as stale (`ProposalStale`: changed proposal, expired, or balance
   snapshot older than `max_snapshot_age_minutes`), re-propose by resuming setup; the old version
   is expired and a new one stored.
4. If nobody will approve, do nothing: at the approval deadline the run is CANCELED, fully
   refunded and its spend report stored (`lifecycle.expire_approval`).
5. Never approve on behalf of the tenant from an agent or a script without a person's identity.
