# 07 · Fix log: phase 1 ("fix now")

Branch `fix/system-design-phase1`, cut from `feat/final-product` at `e3b94c51` (the working tree that is live as rel-2ccee878-11081754, committed as a snapshot at the owner's choice). One commit per finding, with the finding ID in the subject. **Nothing here is deployed, applied, pushed or merged.** Every change to a live system is listed under "Waiting for approval" with its exact command.

Started 2026-11-10. Owner decisions received in this run: the work-in-progress handling (snapshot commit), the research_jobs scrub columns (goal, topic, failure_reason), the hard ceilings ($75 per research run, $25 per Ask conversation). Every other line of the owner's decision list was blank.

## Phase 1 list: roadmap vs the owner's guess

The roadmap's phase 1 (`04-target-architecture.md` §4) is SD-02, SD-01, SD-03, SD-07, SD-10, SD-11, SD-13, SD-19, SD-18, SD-25 and SD-30. The owner expected SD-01, SD-02, alert routing and CI builds. **CI-gated builds (SD-09) are phase 2 in the roadmap**, so they were not done in this run. Alert routing (SD-03) is phase 1 and was done. The order of work followed the owner's order for the two P0s (SD-01, then SD-02), then the roadmap.

## Status

| ID | Status | Commits | Test that proves it |
|---|---|---|---|
| SD-01 | Fixed (live: pending approval of the deploy and migration) | `1ff33563`, `cab76224`, `5667821e` | `tests/system/test_deletion_keeps_ledgers.py` |
| SD-02 | Fixed (ceilings). Switches on: Blocked on decision. Console limits: owner checklist | `32c5784c` | `tests/connectors/test_hard_ceiling.py`, `tests/replica/test_ask_thread_ceiling.py` |
| SD-03 | Fixed pending approval; the address is Blocked on decision | `1306a001` | `infra/stack/tests/plan.tftest.hcl` run `alerts_reach_the_on_call_email` |
| SD-07 | Fixed (live: pending approval of the deploy) | `b441e854`, `c4f23b68`, `ee13dab1` | `tests/connectors/test_rate_fallback.py`, `tests/unit/test_readyz_redis.py` |
| SD-10 | Fixed (live: pending approval of the deploy and migration) | `4497be5b`, `ee13dab1` | `tests/phase3/test_sso_domain_proof.py` |
| SD-11 | Fixed (live: pending approval of the deploy) | `4f441eae`, `ee13dab1` | `tests/replica/test_team_invite.py::test_an_operator_invites_one_more_user_for_a_client` |
| SD-13 | Blocked on decision | none | not testable in code (IAM and Google Workspace settings) |
| SD-19 | Partly fixed: query rewrite Fixed; slow-query flag Fixed pending approval; session timeouts Blocked on decision | `0d67ebc2` | `tests/replica/test_newest_probe_db.py`, `tests/replica/test_mutation_kills_records.py` |
| SD-18 | Fixed (lint). Live data check: Blocked on decision (start the database) | `f4f2774d` | `tests/system/test_migration_force_rls_lint.py` |
| SD-25 | Graceful shutdown: Could not reproduce. Worker concurrency: Blocked on decision | none | n/a |
| SD-30 | Blocked on decision (`my-service`); the rest Fixed pending approval (commands below); orphan secret: Could not reproduce | none | n/a |

## Preflight

- `git status` showed 42 modified and 7 untracked paths on `feat/final-product` (live but uncommitted). At the owner's choice: `f0eb5bbb` commits `docs/system-design/` alone, `e3b94c51` commits the rest as a snapshot, and the fixes branch from there.
- **Baseline test run.** The first baseline ran in the working folder while fixes were being edited, so its result is not clean. Two of its five failures came from edits made during the run. A clean baseline was then run from an archive of `e3b94c51` next to the branch tip run; the comparison is in "Test results" at the end.
- **`terraform plan` (read-only, `infra/envs/staging`)** needed a quota project for two APIs (`GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true`). Before any change of this run: `0 to add, 21 to change, 0 to destroy`. **This is drift: changes nobody wrote in this run.** Two matter:
  - `google_sql_database_instance.main`: `activation_policy NEVER -> ALWAYS`. Any apply that touches the instance **starts the stopped database**.
  - Seven Cloud Scheduler ticks: `paused true -> false`. Any apply that touches them **resumes the ticks** (sweep, schedules, notify relay, metrics, audit verify, isolation check, access expiry).
  - The rest is provider noise: Cloud Run `scaling` blocks and the worker's `startup_cpu_boost` (set outside Terraform), eight dashboards (layout fields), the budget's default `all_updates_rule`, Identity Platform's empty `phone_number` block.
  - So **every apply in this log is a `-target` apply**, never a plain one. Recorded as new finding SD-39.
- **Database role checks (`00-verdict.md`): Blocked on decision.** The owner has not said whether the stopped database may be started, so whether the application role owns tenant tables, is a superuser or can bypass row-level security could not be checked on the live instance. On the local database the runtime roles are NOBYPASSRLS and FORCE holds on every tenant table (the isolation suite passes).

## SD-01 · Tenant deletion wiped the credit ledger

**Reproduced.** `migrations/0053_replica.sql:130` makes `fk_credit_entries_job` `ON DELETE CASCADE`, and `config/deletion.yaml` erased `research_jobs`. The new test planted two ledger rows (430 credits) for a tenant, ran the whole approved deletion, and the ledger went to `(0, 0)`.

**What changed.**
- Migration `0116_credit_ledger_restrict` changes the foreign key to `RESTRICT`. It also takes `research_jobs` off the `erase_rows` allow-list, takes `DELETE` on it away from `tenant_eraser`, and adds `scrub_rows()`. Its reverse migration restores all of this and drops only the new function. Down and up were run on the scratch database.
- `scrub_rows()` empties the owner's chosen columns (`goal`, `failure_reason` to NULL; `topic` to `'purged'`, as the account purge does) under the same approval checks as `erase_rows`.
- `config/deletion.yaml` gains a `scrub` list; `research_jobs` moved there. `tenancy.lifecycle` scrubs after erasing.
- `dry_run` now refuses a plan when an erased table has a cascading or nulling foreign key into a kept table (`cascade_breaches`). The schema test uses the same check.
- Follow-ups:
  - The readiness probe expects schema 0116.
  - The operator dry-run response carries `scrub`/`scrubbed`; the API contract had refused the new field with a 500.
  - The isolation matrix lists `scrub_rows`.

**Tests.**
- `test_tenant_deletion_keeps_the_credit_ledger_and_its_totals`: ledger rows and totals are identical before and after a full deletion, the other tenant is untouched, and the job rows keep their money columns with the words emptied. Failed before, passes now.
- `test_no_erased_table_cascades_into_a_kept_table` and `test_the_plan_check_refuses_a_cascade_into_a_kept_table`: the foreign-key-aware plan check.
- `test_the_database_refuses_to_delete_a_job_that_carries_ledger_rows`: failed before ("DID NOT RAISE").

**Cascade sweep of the whole schema** (every `ON DELETE CASCADE` or `SET NULL`):
- The only one that landed on a money or audit table was `credit_entries -> research_jobs`, which is now fixed.
- `research_jobs -> accounts` is also `CASCADE`. Accounts are never deleted (tombstoned), and since 0116 a job with ledger rows cannot be deleted through it either.
- The others (`chat_turns`, `document_*`, `research_file_*`, `import_run_parts`, `download_jobs`, `raw_files` SET NULL) stay within erased content.

**Left for a human.**
- Deploying with migration 0116 (approval A1).
- Account purge is unchanged; it already scrubbed `research_jobs`.

## SD-02 · Spend was uncapped

**Reproduced.**
- `research_uncapped()` returns early from `spend_guard.check_cap`, and `runs/lifecycle.py` gives the run a $1,000,000 budget.
- `config/chat_models.yaml` allows $1,000 per thread, and nothing reads it.
- The new run test paid a second call with both switches off.

**What changed.**
- **Research runs.** `spend_guard.check_cap` is the one check that every paid call already passes: provider calls in the ledger claim and model calls in `RunDeps`.
  - It now refuses a call that would take the run's real spend (provider ledger plus priced model replies) past `HARD_RUN_CEILING_USD = $75`.
  - This check runs before the caps-off early return, so it holds whatever `RESEARCH_CAPS`, `TOKEN_GUARD` or `DEV_UNCAPPED` say.
  - `HardCeilingReached` is a `DollarCapReached`, so the run ends PARTIAL through the existing `_stop_unexpected` → `end_from_active` path. The reason is recorded as `HardCeilingReached` and shown as "It reached the most one research may spend."
- **Ask.** `/v1/chat/ask` refuses a question on a conversation whose answered turns cost `HARD_THREAD_CEILING_USD = $25` (or the config value, if lower).
  - The refusal is 409 `thread_spend_ceiling`, sent before any model call. The web shows the server's sentence, and a new conversation still answers.
  - A conversation can pass the ceiling by at most the one turn that crossed it.

**Tests.**
- `test_with_both_switches_off_the_hard_ceiling_still_stops_the_run`: provider call refused unpaid, model call refused, run PARTIAL with reason `HardCeilingReached`. Failed before.
- `test_a_question_on_a_thread_at_the_ceiling_is_refused_with_the_switches_off`: $24.99 is answered, $25.00 is refused, a new thread answers. Failed before.

**What a run can cost us against what the customer is charged.**
- A new research reserves 250 credits ($12.50, `config/credits.yaml`), and settlement charges `min(spend, reservation)` (`replica/credits.py:408`). So the customer pays at most $12.50, whatever the run cost.
- With the $75 ceiling, the most we can absorb on one run is $62.50. Recorded runs cost $4.31, $39.76 and $60.70 (Olympus, 2026-11-05), so the gap is real today, not theoretical.
- Ask turns are not charged credits at all, so up to $25 (plus one turn) per conversation is absorbed.

**Tie the ceiling to the reservation? Not as the stop.** A ceiling of 1× the reservation would have stopped two of the three recorded runs as PARTIAL and delivered less than the customer asked for. The gap is a pricing question (SD-20, phase 2), with two options:
- reserve and charge for new research closer to its measured cost (the median of the workspace's own runs, as `usage_estimate` already computes);
- or charge actual spend up to the ceiling.

The ceiling's job is to bound the loss.

**Left for a human.**
- Turning `RESEARCH_CAPS` and `TOKEN_GUARD` on: Blocked on decision (approval A2 once decided).
- Provider console limits (checklist below).
- Deploy (A1).

## SD-03 · Alerts notified nobody

**Reproduced.** `terraform.tfvars` has `alert_channels = []`, and the module passes that list to every policy and to the budget.

**What changed.**
- A new `alert_emails` variable (stack, staging, prod, example tfvars) creates one Cloud Monitoring email channel per address.
- Every page, ticket and budget alert now notifies those channels plus any ids in `alert_channels` (output `alert_channel_ids`).
- `clouderrorreporting.googleapis.com` is enabled.
- Workload alerts stay off: `terraform.tfvars` records that their metrics were unseen on 2026-11-08 and turning them on failed the apply.

**Tests.** The new plan-test run (one email channel, its address, two channels on every alert, Error Reporting on) failed before and passes now. Staging and prod `validate` pass in a scratch copy, so the real backend setup is untouched.

**Left for a human.**
- The address: Blocked on decision.
- The apply: approval A3.
- The test notification: approval A3b. An alert is unproven until the owner confirms the email arrived.

## SD-07 · Losing Redis stopped every paid call

**Reproduced.** `RedisRateStore` raises on any Redis error, and `RateLimiter.acquire` has no fallback (`connectors/ratelimit.py`).

**What changed.**
- `FallbackRateStore` wraps the Redis store.
  - On a Redis error it paces through the process's own bucket at a quarter of the provider rate with a burst of one, and keeps 429 cooldowns locally.
  - It logs `rate_limit_degraded` once (the existing worker log alert pages on it) and goes back to Redis when Redis answers.
- `/readyz` reports `components.redis` (`ok` / `unreachable (paid calls pace in-process)` / `not used`) from a 1 s ping.
- **Different from the register on purpose:** Redis is informational in `/readyz`, not a failing check. `/readyz` is the api and worker **startup probe** (`infra/stack/main.tf:642,676`), so failing it on Redis would turn a Redis outage into a full outage, which is what SD-07 removes.

**Tests.**
- `test_a_redis_outage_paces_paid_calls_in_process_instead_of_failing_them`: failed before, passes now.
- `test_readiness_reports_redis_without_failing_on_it`.
- `test_wiring` now asserts Redis sits behind the fallback.
- The connectors suite passes (238 tests).

**Left for a human.**
- Deploy (A1).
- Memorystore STANDARD_HA and AUTH stay in phase 2.

## SD-10 · Any tenant could claim another company's email domain

**Reproduced.** `ConnectionBody._domains` checks syntax only, and `idp_connection_for_domain` returns the first active connection by slug (0062).

**What changed.**
- **DNS proof.** `identity.domain_proof`: each listed domain needs TXT `_6th-sso.<domain>` = `6th-sso-verification=<sha256(tenant id, domain)>`.
  - The token is bound to the tenant, so it needs no secret or stored state.
  - Create and update check every domain. A missing record is a 422 `domain_unproven` that names the exact record to publish.
- **One tenant per domain.** A domain another tenant's active connection has proven is a 409 `domain_claimed`. The definer function `idp_domain_claimed_elsewhere` returns a boolean only, never which tenant.
- **Migration 0117.** It adds `idp_connections.domains_proven_at`, stamped on a proven save.
  - Discovery returns a slug only for proven domains held by exactly one tenant.
  - JIT admits only proven domains.
  - The reverse migration restores the old discovery and keeps the column (no column is dropped).
- **No web screen exists for SSO connections** (API only), so there is no UI change.

**Tests.** `tests/phase3/test_sso_domain_proof.py`:
- no record → 422 with the record, nothing saved;
- another tenant's record → 422;
- own record → 201 and discovery routes;
- a domain already proven by another tenant → 409;
- unproven or two-tenant domains → discovery says "password";
- JIT refuses an unproven domain.

The phase-3 SSO, discovery and isolation suites pass. Fixtures now mark connections proven by default.

**Behaviour change to know.** Any connection saved before 0117 stops routing its domains (discovery and JIT) until a tenant admin saves it again with the TXT records published. Whether any live tenant uses SSO could not be checked (database stopped).

**Left for a human.**
- Deploy with migration 0117 (A1).
- Showing the claiming tenant to staff (a console view) is not built.

## SD-11 · Staff could enter a tenant without a support grant

**Reproduced.** `POST /operator/v1/console/clients/{tenant_id}/users` created a member and returned the invite link to any staff member; the test got 201 with no grant.

**What changed.**
- The route now answers 403 `support_grant_required` unless the client's admin gave this staff member a **write** grant that is active now: not revoked, `granted_at <= now < expires_at`.
- Read grants and expired grants are refused.
- The console shows the reason.
- Unchanged: adding a brand-new client and renewing its first admin's invite. No client data exists yet, and no client admin exists to grant access.

**Tests.** The operator invite test now covers no grant, read grant, expired write grant (all 403) and active write grant (201). It failed before. Team invite and admin console suites pass.

**Left for a human.**
- Deploy (A1).
- An alert to the tenant admin on a staff-created member is not built. The customer-visible audit entry `client.user_invite` remains.

## SD-13 · Staff MFA is assumed; personal accounts hold the keys

**Reproduced (read-only).**
- `roles/owner` is held by `user:inamdar.sakib90@gmail.com` (a personal account) and `user:rajesh@6th.ai`.
- `operator.py` treats a Google staff token without `amr` as MFA.

**Not changed in code.** The code cannot check another account's 2-step setting, and refusing tokens without `amr` would lock out every Google staff sign-in.

**Blocked on decision** (who replaces the personal account as Owner and operator, and from which Workspace group). The checklist and commands are in approval A6. The session-length mismatch (10 min vs the documented 30 min) is not on the phase-1 list.

## SD-19 · Database guardrails missing

**Reproduced.**
- `NEWEST_POSTING`, `NEWEST_PERSON` and `NEWEST_DETECTION` used `NOT EXISTS` (`replica/current.py`).
- `log_min_duration_statement=-1` (`infra/modules/cloud_sql/main.tf`).
- No session timeouts were set anywhere.

**What changed.**
- The three filters are now index probes like `NEWEST_FACT`, each on its existing `(account_id, key, created_at DESC)` index, so no migration was needed.
- Cloud SQL flag `log_min_duration_statement = 500`.

**Tests.**
- `test_newest_probe_db.py` proves on PostgreSQL that the probes keep exactly the rows the old clause kept: newest per key, and keyless people always. It passed against the old clause too, which shows the two forms are equivalent.
- The structural test failed before (asserts no `NOT EXISTS`).
- The replica suite passes (581 tests).

**Session timeouts: Blocked on decision. My proposal is different from the register, and why:**
- **Where to set them.** Role-level `ALTER ROLE` in a migration does not work here: the login roles are per-environment IAM users, so a migration cannot name them. The reliable place is the connection's `server_settings` in `cloud_sql_engine`, per `SERVICE_ROLE`.
- **Values:**
  - api: `statement_timeout` 30 s, `idle_in_transaction_session_timeout` 120 s;
  - worker: `statement_timeout` 300 s, idle 600 s.
- **Why idle is 120 s, not 60 s.** The Ask route holds a transaction open across the route classifier's model call (`api/routes/ask.py`, the `R.route(...)` call inside `tenant_txn`). A 60 s idle limit could cut live Ask turns.

**Left for a human.**
- Approve or change the timeout proposal.
- Applying the flag (A4) touches the stopped Cloud SQL instance, so it waits for the database decision.

## SD-18 · Migration data changes silently touched zero rows

**Reproduced, with a correction to the audit.** The new lint flags only **4** migrations (0043, 0063, 0079, 0112) that change rows of a FORCE-RLS table without lifting FORCE. The audit's other 12 change rows only **inside function bodies**, which run later under their own role.

**What changed.** `tests/system/test_migration_force_rls_lint.py`:
- It fails on any new migration that updates or deletes rows of a FORCE table without `NO FORCE … FORCE` around the change.
- FORCE tables are read from the migrated database (0002 sets most in a loop, which a text scan cannot see).
- A synthetic case proves it catches an unlifted update and passes a lifted one.
- The four old ones are a shrink-only ratchet.

**Left for a human: the live data check (Blocked on decision: start the database).** Run these read-only queries; a non-zero count means that migration's change never happened:

- 0043:
  ```sql
  SELECT count(*) FROM tombstones WHERE tenant_id IS NULL;
  ```
  Expected 0, since the column is now NOT NULL. If the migration had silently failed, the NOT NULL would have failed too, so 0043 almost certainly applied.
- 0063 backfilled `owner_user_id` on about 100 tables. Per table:
  ```sql
  SELECT count(*) FROM <table> WHERE owner_user_id IS NULL;
  ```
  Or, for the column's placeholder, compare with how 0063 set it. This is the one that matters: if it touched zero rows, old rows may be invisible to their owners under the private-workspace policies.
- 0079:
  ```sql
  SELECT count(*) FROM chat_turns WHERE answer_json IS NOT NULL AND surface IS DISTINCT FROM 'ask';
  ```
- 0112:
  ```sql
  SELECT count(*) FROM connector_connections WHERE kind IN ('gmail','outlook','google_drive','onedrive');
  ```
  Plus the same `connection_id IN (...)` counts on `connector_quarantine`, `connector_items` and `connector_grants`.

`CREATE INDEX CONCURRENTLY` (the register's third item) is not on the phase-1 roadmap step.

## SD-25 · Shutdown and concurrency

- **Graceful shutdown ≥ 30 s: Could not reproduce, because the audit was wrong for Cloud Run.** Cloud Run sends SIGTERM and kills the container 10 seconds later. The worker's `--timeout-graceful-shutdown 8` already fits inside that window, and 30 s cannot be honoured. Instances also stop receiving requests at SIGTERM, so the missing readiness probe does not matter there.
- **Worker concurrency: Blocked on decision.** The Terraform default is already 1 (ADR-0130), but the local staging tfvars sets `worker_concurrency = 4`. A read-only plan with 1 shows the side effect: the queue dispatch limits follow `worker max instances × concurrency`, so **the runs queue drops from 6 concurrent slices to 1**, meaning one research at a time platform-wide. To keep 6 run slots at concurrency 1, worker max instances must go from 3 to 12 (12 instances of 4 vCPU / 12 GiB; check quota and cost). The choice belongs to the owner:
  - **(a)** concurrency 1 and max 12 workers;
  - **(b)** concurrency 1 and fewer research slots;
  - **(c)** keep 4 and record the deviation from ADR-0130.

  My local test edit to the tfvars was reverted; the file is unchanged.

## SD-30 · Cloud project hygiene

**Verified (read-only).**
- **`my-service`:** created 2026-10-19, ingress `all`, runs as the default compute service account. Its image is from `cloud-run-source-deploy`.
- **Default compute service account:** holds `roles/editor`. It is **enabled**, although Terraform's `google_project_default_service_accounts` says `DISABLE`; this drift is invisible to `plan`.
- **Cloud services account:** `979669242383@cloudservices…` holds `roles/editor`.
- **`default` network:** has no instances, but four firewall rules are open, including SSH (`0.0.0.0/0:22`) and RDP (`0.0.0.0/0:3389`).

**Could not reproduce: the orphan `smtp-password` secret.** Terraform manages it (`infra/stack/main.tf:327`), and the invite mailer still uses SMTP. Only the Gmail and Drive connectors were removed.

**Left for a human.** `my-service`: Blocked on decision. The other deletions and IAM removals are approval A5.

## Waiting for approval

Nothing below has been run. Each needs an explicit yes.

**A1 · Deploy the branch to staging** (SD-01, SD-02, SD-07, SD-10, SD-11, SD-19 code; migrations 0116 and 0117).
- **Precondition:** the database must be running, which is a decision, and the release tool refuses a stopped database.
- **Commands:**
  ```bash
  uv run python scripts/release/staging_release.py status
  uv run python scripts/release/staging_release.py build --commit <tip of fix/system-design-phase1> --services api,worker,web
  uv run python scripts/release/staging_release.py deploy docs/deploy/releases/<tag>.json --allow-migrations
  ```
- **What it changes:** migrations, then worker, api and web revisions, each checked.
- **Undo:** the tool rolls traffic back on any failed check. By hand:
  ```bash
  gcloud run services update-traffic <svc> --region us-central1 --to-revisions=<previous>=100
  ```
  The migrations undo with `alembic downgrade 0115` (0117's down keeps its column; 0116's down restores the cascade).
- **Note:** this releases an unmerged branch. Merging stays with the owner.

**A2 · Turn the switches on** (after the decision). In `infra/envs/staging/terraform.tfvars`, set `research_caps = "on"` and `token_guard = "on"`, then:
```bash
cd infra/envs/staging && GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true terraform apply -target='module.stack.module.cloud_run.google_cloud_run_v2_service.svc["api"]' -target='module.stack.module.cloud_run.google_cloud_run_v2_service.svc["worker"]'
```
- **What it changes:** new api and worker revisions with the two variables `on`; it also clears the worker's out-of-band `startup_cpu_boost`.
- **Undo:** set both back to `off` and repeat the command.

**A3 · Alert channels** (after the address). Add `alert_emails = ["<address>"]` to the tfvars, then:
```bash
cd infra/envs/staging && GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true terraform apply -target='module.stack.google_monitoring_notification_channel.alert_email' -target='module.stack.module.observability' -target='module.stack.module.budget' -target='module.stack.module.project.google_project_service.enabled["clouderrorreporting.googleapis.com"]'
```
- **What it changes:** creates the email channel(s), attaches them to the 17 policies and the budget, and enables Error Reporting. The dashboards' layout noise is rewritten as well.
- **Undo:** `alert_emails = []` and the same command. That leaves Error Reporting enabled; to turn it off as well:
  ```bash
  gcloud services disable clouderrorreporting.googleapis.com
  ```

**A3b · One test notification** (after A3). This writes one log line that the `rate_limit_degraded` page alert watches:
```bash
gcloud logging write sd03-alert-test '{"event":"rate_limit_degraded","kind":"test-notification"}' --project=abmgenerator --payload-type=json --severity=WARNING --monitored-resource-type=cloud_run_revision --monitored-resource-labels=service_name=ai-staging-worker,location=us-central1,revision_name=test,configuration_name=ai-staging-worker,project_id=abmgenerator
```
- The owner confirms the email arrived, then closes the incident in Monitoring.
- **Undo:** none needed; the incident auto-closes.

**A4 · Slow-query log flag** (after the database decision):
```bash
cd infra/envs/staging && GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true terraform apply -target='module.stack.module.cloud_sql.google_sql_database_instance.main'
```
- **What it changes:** sets `log_min_duration_statement=500`. **It also sets `activation_policy` ALWAYS, which starts the database.**
- **Undo:** set the flag back to `-1` in `infra/modules/cloud_sql/main.tf` and apply the same target. To stop the database again:
  ```bash
  gcloud sql instances patch ai-staging-pg --activation-policy=NEVER
  ```

**A5 · Project hygiene** (SD-30; `my-service` waits for its decision):

1. Delete the default network's firewall rules, then the network:
   ```bash
   gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp default-allow-icmp default-allow-internal
   gcloud compute networks delete default
   ```
   Undo: `gcloud compute networks create default --subnet-mode=auto`, and recreate the rules if ever wanted.
2. Remove Editor from the default compute account:
   ```bash
   gcloud projects remove-iam-policy-binding abmgenerator --member=serviceAccount:979669242383-compute@developer.gserviceaccount.com --role=roles/editor
   ```
   Undo: the same command with `add-iam-policy-binding`. Do this only after `my-service` is deleted or moved to its own account, because it runs as this account.
3. Disable the default compute account:
   ```bash
   gcloud iam service-accounts disable 979669242383-compute@developer.gserviceaccount.com
   ```
   Undo: `gcloud iam service-accounts enable …`. Same precondition as step 2.
4. Keep Editor on `979669242383@cloudservices.gserviceaccount.com`. It is the Google APIs service agent, and Google advises against removing it; it is not a user-controllable identity.
5. If `my-service` is to go:
   ```bash
   gcloud run services delete my-service --region us-central1
   ```
   **This cannot be undone.** The image stays in Artifact Registry under `cloud-run-source-deploy/my-service`.

**A6 · Owners and staff** (SD-13, after naming the Workspace group):
1. Create a Workspace group, for example `gcp-owners@6th.ai`, with 2-step verification enforced (owner checklist).
2. Grant Owner to the group:
   ```bash
   gcloud projects add-iam-policy-binding abmgenerator --member=group:<group> --role=roles/owner
   ```
3. Check that one group member can sign in. Only then remove the personal account:
   ```bash
   gcloud projects remove-iam-policy-binding abmgenerator --member=user:inamdar.sakib90@gmail.com --role=roles/owner
   ```
4. Remove the address from `staff.external_emails` in the tfvars and apply the api target, as in A2.

Undo for each step: the opposite `add-`/`remove-iam-policy-binding`.

## Owner checklists (provider consoles)

**Spend limits (SD-02).** Suggested monthly limits come from the $500 lost on failed runs and the $75 ceiling; set them to what you can afford to lose in a month.

| Provider | What it is here | Where the setting is | Suggested monthly limit |
|---|---|---|---|
| Anthropic | research and Ask models | console.anthropic.com → Settings → Limits → spend limit (and an email notification at 50%) | $1,000 |
| Moonshot (Kimi) | Ask harness model | platform.moonshot.ai → Billing / usage limits | $200 |
| xAI | selectable Ask model | console.x.ai → Billing → spending limit | $100 |
| Ollama Cloud | `ollama` models in `models.yaml` | ollama.com → Settings → plan and usage (prepaid plan caps spend) | the plan in use |
| Parallel (`p_search`) | web search | platform.parallel.ai → Billing → usage limit | $300 |
| Firecrawl (`p_scrape`) | page reads | firecrawl.dev → Billing → plan credits (prepaid) | the plan in use |
| PredictLeads (`p_signals`) | company signals | account manager or plan (fixed plan) | the plan in use |
| Bitscale (`p_people`) | people search (disabled in code) | bitscale.ai → Billing | $0 until used |
| Sumble (`p_org`) | org data | sumble.com → account → plan | the plan in use |
| Google Cloud | the platform | Billing → Budgets: the $200 budget only alerts. Billing cannot hard-stop. | keep the alert; add the A3 channel |

**Alerting (SD-03):** name the on-call address, then run A3 and A3b and confirm the email arrived.

**Staff MFA (SD-13):** in Google Workspace admin, go to Security → 2-step verification → Enforce for the staff organisational unit. Remove personal Gmail accounts from `STAFF_EXTERNAL_EMAILS`.

## New findings (added to `03-findings.md`)

- **SD-39 (P1).** The Terraform state and the live project disagree on the database's activation policy and on seven paused scheduler ticks, so any untargeted `terraform apply` starts the database and resumes every tick. The default compute service account is also enabled against Terraform's `DISABLE`, and `plan` does not show it.
- **SD-40 (P2).** The log-event alerts (`google_logging_metric.event`) filter on the **worker** service only. Events the api logs, such as sign-in's `rate_limit_degraded`, never reach an alert.
- **SD-41 (P2).** `docs/WIRING.md` was already out of date before this run (`scripts/wiring_audit.py --check` fails), so `make contract` fails on the baseline. It was not regenerated in this run, to keep the commits to their findings.
- **Corrections to the audit:**
  - SD-18 is 4 migrations, not 16.
  - SD-25's "graceful shutdown ≥ 30 s" cannot work on Cloud Run.
  - SD-30's `smtp-password` is not orphaned.

## Test results

Python: `pytest -m "not eval and not live"` on a private scratch database. Web: `vitest`. Each run started from a `git archive` copy of the commit, so edits in the working folder could not change it.

| Run | Commit | Python | Web |
|---|---|---|---|
| Clean baseline | `e3b94c51` | 7661 passed, 3 failed, 29 skipped | 383 passed, 1 failed |
| Branch tip, first run | `f4f2774d` | 7693 passed, 5 failed, 14 skipped | 384 passed |
| Branch tip, after the regression fix | `ee13dab1` | 7682 passed, 1 failed, 29 skipped | (no web change since the first run) |

- **The first branch-tip run found four regressions this run caused:**
  - raw SQL grew in `admin_console.py` and `sso.py` (the route ratchet);
  - a `getLogger` call in `connectors/ratelimit.py` (the telemetry contract);
  - the migration chain head test still named `0115`;
  - the isolation matrix's staff-invite probe was refused 403 by the new SD-11 check before the route ran.

  All four are fixed in `ee13dab1`, without raising any baseline or weakening any check. The queries moved into `admin.support` and `identity.connections`, the logging goes through `connectors.obs`, the head is `0117`, and the matrix plants a write grant for that probe. The affected files (894 tests) pass in the working folder.
- **The 5th first-run failure** (`test_one_retry_owner_per_failure_class_w6`, which runs `gen_loops.py --check`) fails on the baseline too. On the tip it is fixed by the regenerated `docs/architecture/loops.md`.
- **The one failure left on `ee13dab1`** (`test_name_scan_exemption_is_exact_to_companion_file`) runs `git ls-files`, which fails in an archive copy because it is not a git repository. The same failure is in the baseline. The test passes in the repository working folder.
- **The baseline's other two failures:**
  - `test_xlsx_is_deterministic` passes on the tip, both in the copy and in the repository;
  - the web `documents.test.tsx` stage timing failure passes on the tip.

  This run did not touch either area.
- `scripts/wiring_audit.py --check` reports drift on both the baseline and the tip (SD-41, already there before this run). `check_guarantees.py`, `gen_loops.py --check`, `check_telemetry.py`, `lint-imports` (21 kept) and `mypy` on the changed modules all pass on the tip.

