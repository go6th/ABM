# Release rel-a0b2db04 to GCP staging (2026-11-04, ~15:25 UTC)

This is a working-tree snapshot (git tree a0b2db04, uncommitted) of the ingestion hardening: PR1–PR6
and the Ask deep-dive sheet citations.

The build was one Cloud Build (06470029, E2_HIGHCPU_8, 6 min 29 s).

| Service | New revision | Rollback (quick revert only) |
|---|---|---|
| api | ai-staging-api-00115-hdd | ai-staging-api-00114-z8z |
| worker | ai-staging-worker-00098-5t6 | ai-staging-worker-00097-6s7 |
| web | ai-staging-web-00070-vf4 | ai-staging-web-00069-8hz |

**Migrations.** The migrate job ran 0105 → 0106 → 0107 → 0108 → 0109 → 0110. Older code ignores the
new columns and tables, but it does not know the document states `needs_account`, `scan_failed` and
`abandoned`. That is why a rollback is a quick revert only.

**Checks.**
- `/readyz` returned 200, `/v1/models` 401, and `/` 307.
- The new revisions logged no ERROR lines.
- The worker logged two warnings, both known: `db.role_pending` (the ADR-0123 Terraform was never
  applied) and `research.caps_off` (owner's decision).

**Digests.** Written to `infra/envs/staging/terraform.tfvars` (git-ignored). The previous file is
backed up in the session scratchpad.

**Not applied.** The quarantine bucket Terraform: the auto-mode safety check refused
`terraform apply` twice. The plan is reviewed: 4 resources to add, 0 to change, 0 to destroy. Its
text is in `docs/plans/prs/quarantine-tfplan.txt`. Until the bucket exists:
- `QUARANTINE_BUCKET` is unset, so uploads keep using the `quarantine/` prefix of the documents bucket;
- the `ai-staging-documents-abandon` scheduler tick does not exist, so abandoned uploads are not
  marked yet.

To finish, the owner:

1. Applies the plan from `infra/envs/staging`, or re-plans with RUNBOOK 2.1a:

   ```bash
   GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true /snap/bin/terraform plan -out=q.tfplan -target=module.stack.module.gcs.google_storage_bucket.quarantine -target=module.stack.module.gcs.google_storage_bucket_iam_member.quarantine_create -target=module.stack.module.gcs.google_storage_bucket_iam_member.quarantine_view -target=module.stack.module.gcs.google_storage_bucket_iam_member.quarantine_process
   ```

   ```bash
   GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true /snap/bin/terraform apply q.tfplan
   ```

2. Sets `QUARANTINE_BUCKET` on the api:

   ```bash
   gcloud run services update ai-staging-api --region us-central1 --update-env-vars QUARANTINE_BUCKET=ai-staging-quarantine
   ```

3. Sets it on the worker:

   ```bash
   gcloud run services update ai-staging-worker --region us-central1 --quiet --container=worker --update-env-vars QUARANTINE_BUCKET=ai-staging-quarantine
   ```

**Also off on staging.** The file-analysis token pause only applies once the worker runs with
`TOKEN_GUARD=on`, because research caps are off there.

## Redeploy rel-0d5882c7 (2026-11-04, ~16:20 UTC)

This release adds the fixes for the 6 failures of the final full suite.

**Test fixes:**
- `needs_account` expectations in the tests;
- the Ask indexing-notice query moved into `documents.repo`;
- `research_tables` uses the obs logger;
- the streamed Ask answer always carries `notices`.

**Event-loop speedups:**
- `strip_tool_names` uses one precompiled pattern;
- `models.yaml` is parsed with the C YAML loader and cached per file state;
- `fan_out` runs a fixed pool of `limit` workers instead of one task per item.

**Also:** a web lint fix. No migration.

| Service | New revision | Rollback |
|---|---|---|
| api | 00116-5d5 | 00115-hdd |
| worker | 00099-m6c | 00098-5t6 |
| web | 00071-b9m | 00070-vf4 |

**Checks.** `/readyz` returned 200, `/v1/models` 401, and `/` 307. The new revisions logged no ERROR
lines. Digests are in `terraform.tfvars`.
