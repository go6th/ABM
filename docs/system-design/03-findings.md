# 03 · Findings register

Sorted by severity, then by impact. Evidence labels: **V** verified (`path:line` or `gcloud` command), **I** inferred (basis given), **NF** not found (where looked). Audited: branch `feat/final-product`, working tree with uncommitted changes, and live project `abmgenerator` on 2026-11-10.

**Severity.** P0: can leak data across tenants, expose an unauthenticated endpoint, lose or double-charge money, or lose data. P1: will cause outages, block growth, or fail an enterprise security review. P2: hygiene and maintainability.
**Effort.** S: up to 2 days. M: up to 2 weeks. L: more than 2 weeks.

What was looked for and **not** found, which matters as much as the findings below: no unauthenticated data endpoint (V all 229 routes classified, `api/guard.py:64-69`), no cross-tenant read path through RLS, object keys, cache keys or vector search (V `migrations/0002_rls.sql:19-113`, `src/tenancy/storage.py:183-220`, `src/tenancy/keys.py:13-21`), and no way to reserve or settle credits twice (V `src/account_intelligence/replica/credits.py:295-408`, `migrations/0053_replica.sql:131`).

## Summary

| ID | Sev | Dim | Finding | Effort |
|---|---|---|---|---|
| SD-01 | P0 | F | Tenant deletion would cascade-delete the credit ledger | S |
| SD-02 | P0 | J/H | Spend is uncapped on the live environment; spend above the reservation is absorbed silently | S |
| SD-03 | P1 | L | Alerts exist but notify nobody; no error tracking | S |
| SD-04 | P1 | F/K | Personal data cannot be deleted on request; retention is not enforced | L |
| SD-05 | P1 | H/K | Tenant and personal data go to model providers with the data-class guard off; sub-processor list omits them | M |
| SD-06 | P1 | F/J | Database is single-zone, 1 vCPU, 7-day backups, no cross-region copy | M |
| SD-07 | P1 | J | Redis is a single node without AUTH; its loss stops every paid call | S |
| SD-08 | P1 | E | A run can be ended or lose its next slice through queue waits and swallowed task names | M |
| SD-09 | P1 | M | What runs in the cloud was not built or checked by CI; infrastructure drifts from Terraform | M |
| SD-10 | P1 | C | Any tenant admin can claim another company's email domain for SSO discovery | S |
| SD-11 | P1 | D | Staff can enter a tenant without a support grant through the invite link | S |
| SD-12 | P1 | D | Audit log is not tamper-evident and nightly verification stops at 50,000 rows | M |
| SD-13 | P1 | C/K | Staff MFA is assumed for Google accounts; a personal Gmail is project owner and operator | S |
| SD-14 | P1 | A | No owned domain; the product is served on an sslip.io name of the IP | S |
| SD-15 | P1 | H/G | Ask web tools bypass the ledger, limiter and breaker; model-written queries can carry workspace data out | S |
| SD-16 | P1 | I | Throughput ceiling of 6 concurrent slices, platform-wide provider ceilings, sequential tick loops | M |
| SD-17 | P1 | M | One environment serves customers and testing; flags differ from Terraform defaults | M |
| SD-18 | P1 | F | Data changes inside migrations silently touch zero rows under forced RLS | S |
| SD-19 | P1 | F/I | No statement timeouts, slow-query log off, known saturating query shapes remain | S |
| SD-20 | P1 | G/J | No payment provider; credit pool not enforced on new research | M |
| SD-21 | P2 | C | Some admin actions skip step-up despite comments saying otherwise | S |
| SD-22 | P2 | B/D | Operator and public routers authenticate per handler; CI does not check it | S |
| SD-23 | P2 | B | Three idempotency mechanisms; research replay has side effects; confirm-twice is a client flag | S |
| SD-24 | P2 | B | Error contract strips handler detail | S |
| SD-25 | P2 | E | 8 s graceful shutdown, ineffective drain flag, worker concurrency 4 against ADR-0130 | S |
| SD-26 | P2 | G | Circuit-breaker state is per instance; model calls have no breaker | S |
| SD-27 | P2 | L | Request id stops at Cloud Tasks; trace id not in the Cloud Logging field; worker absent from the 5xx alert | S |
| SD-28 | P2 | E | Two run engines and several unwired packages and dead modules | M |
| SD-29 | P2 | F | Checkpoint, ledger and event tables grow forever | M |
| SD-30 | P2 | K | Cloud project hygiene: stray service, default VPC with open SSH/RDP, Editor on default SAs, orphan secret, ops jobs with client domains | S |
| SD-31 | P2 | F | Object erase is fire-and-forget on a CPU-throttled api; tenant deletion misses one prefix | S |
| SD-32 | P2 | B/I | Upload limits: 20 GiB per file, no count or storage quota; library upload may exceed Cloud Run's 32 MiB HTTP/1 limit | S |
| SD-33 | P2 | I | Long Ask streams and 1 s-polling SSE hold web and api slots; api gen1 with in-process embedding model | M |
| SD-34 | P2 | F | Stop and settlement lock rows in opposite orders (deadlock → 500) | S |
| SD-35 | P2 | K | Container scanning and Error Reporting disabled in the project; CI scans do not gate releases | S |
| SD-36 | P2 | H | Model ids are aliases, not pinned snapshots; routing.yaml contradicts providers.yaml | S |
| SD-37 | P2 | M | Docs and docstrings disagree with code in 17 places | S |
| SD-38 | P2 | K | LibreOffice runs without the seccomp sandbox; seccomp under Cloud Run is unverified | S |

---

## P0

### SD-01 · Tenant deletion would cascade-delete the credit ledger · F · S
- **Finding.** The tenant deletion plan erases `research_jobs` but marks `credit_entries` "never delete". `credit_entries` has a foreign key to `research_jobs` with `ON DELETE CASCADE`, so erasing the jobs silently erases the billing ledger. `check_plan` only compares table names, not foreign keys.
- **Evidence.** V `config/deletion.yaml:108` (research_jobs in erase list), `config/deletion.yaml:171` (credit_entries never), `migrations/0053_replica.sql:130` (`fk_credit_entries_job … ON DELETE CASCADE`), `migrations/0106_import_runs.sql:117` (`erase_rows` allow-list includes research_jobs), `src/tenancy/lifecycle.py:93-100`. The account purge avoids this by scrubbing instead of deleting jobs (V `deletion.yaml:355,368`). The tenant plan is `status: proposal` but is exposed in the operator admin (V `operator_admin.py:291-319`).
- **Risk.** The first tenant offboarding destroys the record of what that customer was charged. Disputes and audits cannot be answered.
- **Fix.** Change the FK to `ON DELETE RESTRICT` and scrub `research_jobs` (as the account purge does) instead of deleting it; extend `check_plan` to walk FK cascades from erased tables into "never" tables and refuse the plan. Add a test. *Skipped:* billing history loss on every offboarding.

### SD-02 · Spend is uncapped on the live environment; spend above the reservation is absorbed · J/H · S
- **Finding.** `RESEARCH_CAPS=off` and `TOKEN_GUARD=off` on api and worker. With caps off a run gets a $1,000,000 budget, a 1,000,000 call cap and a 1,000,000 slice ceiling; retries rise to about ten and page limits lift. Only "three slices with no progress" stops a run. Settlement charges `min(spend, reserved)`, so a new research is charged at most 250 credits ($12.50) whatever it cost. The monthly credit pool is not enforced on new research. The $200 GCP budget only alerts and does not see Anthropic, Ollama or data-provider bills. Ask passes each model's full output limit and `max_spend_usd_per_thread` is parsed but never enforced.
- **Evidence.** V `gcloud run services list` (env RESEARCH_CAPS=off, TOKEN_GUARD=off on both); `src/account_intelligence/runs/lifecycle.py:265-268`; `src/account_intelligence/dev_limits.py:10-47`; `src/account_intelligence/app/container.py:431`; `src/account_intelligence/connectors/settings.py:271-311`; `src/account_intelligence/replica/credits.py:408`; `src/account_intelligence/api/routes/research.py:190` (`enforce_pool=False`); `infra/modules/budget/main.tf:1-3`; `src/account_intelligence/replica/model_answer.py:705`; `src/account_intelligence/chat/registry.py:83,186-193` (parse only; NF enforcement).
- **Risk.** One runaway run or Ask thread can cost hundreds of dollars that are neither blocked nor billed. The project owner's notes record about $500 already lost on failed runs (I, from project memory).
- **Fix.** (1) Turn `RESEARCH_CAPS` and `TOKEN_GUARD` on in the live environment now. (2) Add a hard platform ceiling per run and per Ask thread that no switch can lift (for example 4× the estimate), enforced in the ledger claim and in the Ask loop. (3) Set spend limits in the Anthropic and provider consoles. (4) Alert on spend per hour from the ledger. *Skipped:* unbounded loss per run.

## P1

### SD-03 · Alerts exist but notify nobody · L · S
- **Finding.** 17 alert policies are enabled (api down, web down, 5xx, runs stalled, worker lost, DB CPU/memory/disk, isolation check failed, migration failed, …) and every one has zero notification channels. The project has no notification channels at all. Workload and SLO-burn alerts are disabled. Mail is removed. There is no error tracker (Error Reporting API not enabled; no Sentry or similar). The only message that reaches a person is the billing budget email.
- **Evidence.** V `gcloud monitoring policies list` (17 policies, `notificationChannels` empty), `gcloud beta monitoring channels list` → `[]`; `infra/envs/staging/terraform.tfvars` (`alert_channels = []`, comment "notify nobody"); `infra/modules/observability/main.tf:41-45`; `gcloud services list --enabled` (no `clouderrorreporting`); NF Sentry/Bugsnag/Rollbar in `src/`, `web/`, `pyproject.toml`.
- **Risk.** Customers find outages before the team does. A stuck queue or a stopped database can last days.
- **Fix.** Create two channels (email to an on-call alias, plus Slack or SMS), put them in `alert_channels`, enable workload alerts, enable Error Reporting (structured logs already carry exceptions). Name an owner per alert in the runbook. *Skipped:* outages are discovered by customers.

### SD-04 · Personal data cannot be deleted on request; retention is not enforced · F/K · L
- **Finding.** No data-subject request flow exists (`dsar_requests` table has no code users). Person erasure exists but is deliberately not exposed. Retention windows are written down but nothing enforces them; the scheduler entry was removed because the route was never built. Tenant deletion keeps `people`, `person_*`, `llm_calls.output_json`, `tool_calls.response`, `account_runs` (holds the run goal), `users`, and misses objects under `prepulled/<tenant>/`. LangGraph checkpoints are never pruned. Erased objects remain recoverable 7–14 days (versioning + soft delete) and in backups for 7 days. A named person's data is copied into at least 14 places with no person-keyed index.
- **Evidence.** V `src/people/erasure.py:1-7` (not exposed), NF callers; NF `dsar` in `src/`, `web/`; `infra/stack/main.tf:817-820`; `src/account_intelligence/runs/sweeper.py:33-35`; `config/section11_spec.yaml:48`; `config/deletion.yaml:142-157`; `src/tenancy/storage.py:62-86` vs `src/spend/upload.py:360`; `gcloud storage buckets list` (versioning, soft delete); doc disagreement `docs/registers/retention.yaml` header.
- **Risk.** The product stores data about people who never agreed to it. An erasure request, a regulator or an enterprise buyer's DPA review cannot be satisfied, and the docs claim controls that do not run.
- **Fix.** Build in this order: (1) a person-keyed erasure job (tombstone + scrub across the listed tables, GCS, Redis index) exposed to tenant admins and staff, with an audit event; (2) a scheduled retention job per class in `docs/registers/retention.yaml`, starting with checkpoints (7 days after terminal) and raw payloads; (3) add `prepulled/` to tenant prefixes; (4) state the 7–14 day backup/soft-delete window in the privacy notice. *Skipped:* legal exposure and a failed enterprise security review.

### SD-05 · Tenant and personal data go to model providers with the data-class guard off · H/K · M
- **Finding.** The model data-class guard is "off". Every model entry allows only `public_web` and says zero retention is not verified, yet Ask sends whole uploaded files, table rows and people rows, and research sends people data and scraped text. Kimi runs through Ollama Cloud, a separate company from Moonshot. The sub-processor register lists data providers only.
- **Evidence.** V `config/models.yaml:202-290`; `src/account_intelligence/replica/model_answer.py:695-715`; `gcloud run services list` (`MOONSHOT_BASE_URL=https://ollama.com/v1`); `docs/privacy/subprocessors.md:10-16`.
- **Risk.** Customer documents and personal data reach processors the customer was never told about, under terms nobody has checked. Fails any enterprise vendor review.
- **Fix.** Sign or confirm zero-data-retention terms with Anthropic (or move to Vertex AI in-region, already supported by `LLM_ACCESS=vertex_ai`); decide whether Ollama Cloud is acceptable for tenant data and block it per tenant otherwise; turn the guard on with per-tenant data classes; update the sub-processor list (add Google Cloud, Anthropic, Ollama). *Skipped:* contractual and regulatory exposure.

### SD-06 · Database is single-zone, 1 vCPU, 7-day backups, no cross-region copy · F/J · M
- **Finding.** Cloud SQL `db-custom-1-3840` (1 vCPU, 3.75 GB), `availabilityType ZONAL`, 7 retained backups, 7-day point-in-time recovery, no replica, no cross-region backup location. It carries the API, the ledger, LangGraph checkpoints, governor leases and every tick. It saturated on 2026-11-01.
- **Evidence.** V `gcloud sql instances list`; `gcloud sql backups list`; `src/account_intelligence/replica/current.py:28-44` (incident note).
- **Risk.** A zone failure is a full outage until a restore; a bad migration or deletion noticed after a week is unrecoverable; one heavy query stalls everyone.
- **Fix.** `REGIONAL` availability, 2 vCPU minimum (ADR-0131 already proposes the sizing rule), 30 retained backups, a cross-region backup location, and a quarterly restore drill with measured RTO. *Skipped:* RPO up to the last backup in a regional loss; RTO measured in hours.

### SD-07 · Redis is a single node without AUTH; its loss stops every paid call · J · S
- **Finding.** Memorystore BASIC (no replica, no failover), AUTH disabled, persistence off. The provider rate limiter has no fallback, so a Redis error fails every paid call; per-user limits fail closed with 503 on uploads, resume, exports and downloads. The cache index stores provider response content for 7 days, contrary to its docstring.
- **Evidence.** V `gcloud redis instances list`; `src/account_intelligence/connectors/ratelimit.py:163-184`; `src/identity/rate_limit.py:121-194`; `src/account_intelligence/connectors/cache.py:15-16,258-276`; `src/account_intelligence/api/routes/health.py:102` (health does not ping Redis).
- **Risk.** Memorystore maintenance or a node failure halts research and file actions platform-wide.
- **Fix.** STANDARD_HA tier with AUTH on (the client already pins host and CA); let the provider limiter fall back to a conservative in-process bucket on Redis errors (as sign-in already does); store only references in the index; add a Redis ping to `/readyz`. *Skipped:* a cheap component becomes a platform-wide outage.

### SD-08 · Runs can be ended or lose their next slice · E · M
- **Finding.** Between slices a run holds no lease and no heartbeat. If its next slice waits in the runs queue more than 600 s (only 6 concurrent dispatches), the sweeper ends it PARTIAL `worker_lost`. Nothing recovers a RUNNING run whose next-slice task was never created (enqueue after approve, cap approve, pause resume, timeout expiry). A slice that outlives the 1800 s dispatch deadline is retried while still running and can be resumed twice; if the next slice's attempts are exhausted, re-creating its name hits the Cloud Tasks name tombstone and `AlreadyExists` is treated as success. One approval-timeout task name per run means a second pause's timeout is dropped. Auto-resume is off.
- **Evidence.** V `src/account_intelligence/workflow/runner.py:206-219,305-313`; `src/account_intelligence/connectors/ledger.py:317-323`; `migrations/0050_durable_runs.sql:15-24`; `src/account_intelligence/runs/sweeper.py:120-163`; `src/account_intelligence/runs/recovery.py:131-176`; `src/account_intelligence/platform/gcp/adapters.py:155-160,221-226`; `gcloud tasks queues list` (runs max concurrent 6); `src/account_intelligence/replica/auto_resume.py:30-32`. Failure sequences are I from these.
- **Risk.** Customers pay for runs that end early through no fault of theirs; the 7th concurrent run is at risk by design.
- **Fix.** Record `next_slice_due_at` on the run when releasing the lease and make the orphan rule ignore runs whose next slice is queued; add recovery for RUNNING runs with no slice in flight; on `AlreadyExists` for a slice, check the run's slice counter and enqueue under a new suffix if needed; name timeout tasks per pause (`…-approval-timeout-{pause_no}`); keep slice + overhead well under 1800 s (for example 1500 s). *Skipped:* lost runs grow with load.

### SD-09 · What runs in the cloud was not built or checked by CI · M · M
- **Finding.** The live services are commit `2ccee878` plus an uncommitted patch, built with Cloud Build from a laptop by `scripts/release/staging_release.py`, which runs no tests, no Trivy and no gitleaks. The GitHub deploy workflow fires only on `main`, which is weeks behind the deployed branch (245 commits). Services carry the `gcloud` client annotation, not Terraform's. Container vulnerability scanning is disabled in Artifact Registry. No Cloud Build triggers exist.
- **Evidence.** V `docs/deploy/releases/live.json` (patches); `scripts/release/staging_release.py:1-21,256-315` (NF pytest/trivy/gitleaks); `.github/workflows/deploy-staging.yml:6-31`; `git log` (main vs branch); `gcloud run services list` (annotation `client-name: gcloud`); `gcloud artifacts repositories list` (`SCANNING_DISABLED`); `gcloud builds triggers list` → `[]`.
- **Risk.** A change that fails tests or carries a known CVE can reach customers; nobody can rebuild exactly what runs; Terraform cannot be trusted as a description of the cloud.
- **Fix.** Make the release script refuse a dirty tree and require a green CI run for the exact commit (check the GitHub status API), or run the release from CI on a protected branch; enable `containerscanning.googleapis.com`; run `terraform plan` weekly and alert on drift. *Skipped:* untested code in production.

### SD-10 · Any tenant admin can claim another company's email domain for SSO discovery · C · S
- **Finding.** `verified_domains` on an IdP connection is admin-typed with only a syntax check. Discovery returns the first active connection by slug that lists the domain. A tenant can list `bigcorp.com` and route BigCorp users who type their work email to its own IdP, and JIT-provision them into its own tenant.
- **Evidence.** V `src/account_intelligence/api/routes/sso.py:297,309-315`; `migrations/0062_sso_discover.sql:8-14`; NF ownership proof in `src/identity/connections.py`.
- **Risk.** Cross-tenant phishing of corporate credentials and confusion of identities; fails an enterprise SSO review. Not a direct data read (tenant comes from the connection).
- **Fix.** Require DNS TXT verification before a domain is "verified", make `(domain)` unique across active connections, and show the claiming tenant to staff. *Skipped:* one malicious customer can target another's staff.

### SD-11 · Staff can enter a tenant without a support grant · D · S
- **Finding.** The operator console's invite-user route creates a member with any non-admin role and returns the invite link to the operator, who can accept it and sign in as that member. Support grants (time-boxed, customer-approved) are meant to be the only path.
- **Evidence.** V `src/account_intelligence/api/routes/admin_console.py:400-425`; `src/account_intelligence/api/team_invites.py:8-9`; contradicts `src/admin/support.py:7`. Mitigation: a customer-visible audit entry `client.user_invite` (V `admin_console.py:412-414`).
- **Risk.** Staff access to customer data outside the customer's control; fails the "how does your staff access our data" question.
- **Fix.** Send invite links only to the invitee (once mail exists) or show them only to tenant admins; require a support grant for staff-created invites; alert the tenant admin on any staff-created member. *Skipped:* the support-grant control is bypassable.

### SD-12 · Audit log is not tamper-evident; verification stops at 50,000 rows · D · M
- **Finding.** The chain is an unkeyed SHA-256, so anyone who can write the table can rewrite and re-hash it. Nothing writes to the audit-archive bucket, and its retention policy is not locked. The nightly verifier restarts at row 0 each night and stops after 50 × 1,000 rows, so rows above 50,000 per tenant are never checked and the truncation check never runs for large tenants.
- **Evidence.** V `src/audit/events.py` (`chain_hash`); `src/audit/chain.py:69,75,100`; `src/account_intelligence/api/routes/tasks.py:349`; `gcloud storage buckets describe gs://ai-staging-audit-archive` (no lock); NF writers for the archive in `src/`, `scripts/`.
- **Risk.** The audit trail cannot prove it was not altered; the nightly check gives false comfort.
- **Fix.** Verify incrementally from the last checked `seq`; export a daily signed head hash (KMS asymmetric sign) and the day's rows to the archive bucket; lock the retention policy. *Skipped:* audit evidence would not hold up in a dispute.

### SD-13 · Staff MFA is assumed; personal accounts hold the keys · C/K · S
- **Finding.** For Google staff sign-in the code sets `mfa = True` when `amr` is absent, so two-step verification depends on each account's own setting. A personal Gmail is listed as an external staff operator and is a project Owner. Staff sessions effectively last 10 minutes because every route requires a Google sign-in less than 600 s old (documented as 30 min idle).
- **Evidence.** V `src/account_intelligence/api/operator.py:136-151,257,285`; `gcloud run services list` (`STAFF_EXTERNAL_EMAILS`, `STAFF_OPERATOR_EMAILS`); `gcloud projects get-iam-policy` (owners).
- **Risk.** A phished personal Google account without 2-step verification gets full staff and cloud owner access.
- **Fix.** Use Workspace accounts only, enforce 2-step verification in Workspace admin, remove personal accounts from Owner and the staff allow-list, use group-based IAM with least privilege, and fix the session length to match the doc (re-check step-up only for sensitive actions). *Skipped:* one personal account compromise is a full breach.

### SD-14 · No owned domain · A · S
- **Finding.** The product is served at `136-81-60-29.sslip.io`, a third-party wildcard DNS service that encodes the IP. The managed certificate, OAuth redirect URIs, `PUBLIC_BASE_URL`, cookie scope and HSTS all hang off that name.
- **Evidence.** V `gcloud compute ssl-certificates list`; `gcloud dns managed-zones list` → `[]`; `gcloud run services list` (`PUBLIC_BASE_URL`, `OPERATOR_GOOGLE_REDIRECT_URI`).
- **Risk.** Depends on a free third-party DNS service; looks untrustworthy to buyers; tenant SSO setups would have to change later.
- **Fix.** Register the product domain, add a Cloud DNS zone, issue a managed cert for it, update `PUBLIC_BASE_URL`, redirect URIs and IdP configs before the first enterprise SSO setup. *Skipped:* every customer SSO integration has to be redone later.

### SD-15 · Ask web tools bypass cost and egress controls · H/G · S
- **Finding.** Ask's `web_search` and `read_web_page` call Parallel directly with a fresh HTTP client: no ledger row, no rate limiter, no breaker, no retry. `read_web_page` skips the DNS public-address check and the account allow-list (it may only read URLs this turn's search returned). The model writes the search queries and has the tenant's files in context, so workspace content can leave in a query.
- **Evidence.** V `src/account_intelligence/replica/web_tools.py:17-19,57-77,231-321`.
- **Risk.** Unmetered spend and an exfiltration channel driven by prompt injection from an uploaded file or web page.
- **Fix.** Route both tools through `call_need` (ledger, limiter, breaker, URL policy); cap query length and strip long verbatim spans from queries; log queries per turn for review; make web tools a per-tenant setting, default off for tenants with confidential uploads. *Skipped:* uncontrolled egress of customer content.

### SD-16 · Throughput ceiling and first bottlenecks · I · M
- **Finding.** At most 6 slices run at once (runs queue), worker max 3 × concurrency 4, platform-wide provider ceilings of 4 calls (8 for search) shared by all tenants, a 1-vCPU database, and ticks that loop over tenants sequentially inside fixed deadlines (metrics: 120 s every 2 min). Scheduled starts (10 per tick, 3 per tenant) are switched off.
- **Evidence.** V `gcloud tasks queues list`; `gcloud run services list`; `config/parallel.yaml:96-136`; `src/account_intelligence/api/routes/tasks.py:344-356,392-416,442-452`; `src/account_intelligence/replica/schedules.py:221-224`.
- **Risk.** Around 6 concurrent research runs the platform queues work, and SD-08 then ends runs. Tick work grows with tenant count and overruns deadlines.
- **Fix.** See the capacity section in `02-audit.md` (I). Raise worker max instances and the runs queue together, keep concurrency 1 per ADR-0130, move per-tenant tick work into fan-out tasks, set provider ceilings from contracted quotas. *Skipped:* growth stalls at single-digit concurrent runs.

### SD-17 · One environment serves customers and testing · M · M
- **Finding.** Only "staging" exists. It holds client data (ops jobs name client domains), runs with caps off, and is switched on and off by hand. `infra/envs/prod` has never been applied.
- **Evidence.** V `gcloud run jobs list` (purge/diag args name a client domain); `gcloud run services list`; `infra/envs/prod/` contains only `main.tf` and an example tfvars.
- **Risk.** Experiments and outages hit customers; caps-off testing settings are the production settings.
- **Fix.** Create a separate production project from the same Terraform stack with caps on, HA database and alert channels; keep staging for testing with synthetic or consented data. *Skipped:* every test is a production change.

### SD-18 · Data changes in migrations silently touch zero rows · F · S
- **Finding.** RLS policies are scoped `TO app_rw` and `FORCE` applies to the table owner, so the migrator sees no rows. Sixteen migrations contain `UPDATE`/`DELETE` without lifting `FORCE`; only 0115 does it. All pending revisions run in one transaction with plain `CREATE INDEX`.
- **Evidence.** V `migrations/0002_rls.sql:38,52`; `migrations/0115_remove_owner_only.sql:6-22` (header records the 2026-11-08 failure); grep of up-migrations 0017, 0018, 0043, 0047, 0048, 0060, 0063, 0079, 0092, 0093, 0096, 0102, 0103, 0105, 0106, 0112; `migrations/env.py:69-83`.
- **Risk.** Backfills that "succeeded" may not have happened, leaving data inconsistent; long locks during multi-revision deploys.
- **Fix.** Add a migration lint rule: any DML on a FORCE-RLS table must sit between `NO FORCE`/`FORCE`; re-run a data check for the 16 listed migrations against the live DB; use `CREATE INDEX CONCURRENTLY` in separate non-transactional revisions. *Skipped:* silent data drift.

### SD-19 · Database guardrails missing · F/I · S
- **Finding.** No `statement_timeout` or `idle_in_transaction_session_timeout` anywhere, slow-query logging off (`log_min_duration_statement=-1`), and three filters (`NEWEST_POSTING`, `NEWEST_PERSON`, `NEWEST_DETECTION`) still use the `NOT EXISTS` self-join that saturated the database on 2026-11-01. A hung request holding `client_settings FOR UPDATE` blocks every reservation for that tenant.
- **Evidence.** V `gcloud sql instances list` (flags); NF timeouts in `src/`, `migrations/`, `infra/`; `src/account_intelligence/replica/current.py:28-56`.
- **Risk.** A repeat of the 2026-11-01 saturation; invisible slow queries.
- **Fix.** Role-level `statement_timeout` (30 s api, longer for worker roles) and `idle_in_transaction_session_timeout` (60 s); `log_min_duration_statement=500`; rewrite the three filters as index probes like `NEWEST_FACT`. *Skipped:* recurring outages.

### SD-20 · Billing is not productised · G/J · M
- **Finding.** No payment or invoicing provider. Credits are a manual monthly pool set by staff, and the pool does not block new research. Credit placeholders are labelled as placeholders.
- **Evidence.** NF `stripe|invoice|paddle|chargebee` in `src/`, `config/`, `infra/`; V `config/credits.yaml:1-20`; `research.py:190`; `admin_console.py:484,983`.
- **Risk.** Revenue depends on manual reconciliation; customers can exceed what they bought.
- **Fix.** Enforce the pool (`enforce_pool=True`) with a clear "out of credits" state; add invoicing from `credit_entries` monthly (Stripe Billing or manual invoices generated from the ledger). *Skipped:* revenue leakage.

## P2

### SD-21 · Step-up gaps on some admin actions · C · S
V `src/account_intelligence/api/policy.py:99,285-289` (no `action`, so no step-up, contrary to the comment); `routes/connectors.py:39-51` (`step_up=False`). **Risk:** a stolen session can approve spend pauses or mark documents authoritative. **Fix:** add the `approve_cap` / `manage_settings` actions; add a CI check that admin-only writes carry an action. *Skipped:* weaker admin controls.

### SD-22 · Operator and public routers authenticate per handler · B/D · S
V `src/account_intelligence/api/routes/__init__.py:86-96`; all 54 operator handlers currently declare `require_staff` or `require_operator` (V route table). `scripts/check_route_catalog.py` checks policy shape, not dependencies. **Risk:** one forgotten dependency is an unauthenticated operator route (still behind Cloud Run IAM). **Fix:** put `require_staff` on the router and let `require_operator` refine it; extend the catalog check. *Skipped:* latent auth bypass.

### SD-23 · Idempotency is three mechanisms · B · S
V `api/idempotency.py` unused (NF importers); `POST /v1/runs` uses `Idempotency-Key` (`runs.py:120,171-200`); `POST /v1/research` uses body `request_id` without comparing the body and runs `add_account`/`name_account` and an audit write before detecting the replay (`research.py:170-217`); two concurrent identical requests both take a research-limit unit (`research.py:175`); "confirm twice" is two booleans the client always sends (`web/components/UsageProceed.tsx:16`). **Risk:** confusing replays, small over-counts. **Fix:** one middleware keyed on `Idempotency-Key` + body hash for every paid POST; make the second confirmation a server-issued estimate token. *Skipped:* inconsistent client behaviour.

### SD-24 · Error contract strips handler detail · B · S
V `src/account_intelligence/api/problem.py:30-40` replaces `HTTPException.detail` with the status title; 402 has no title. **Risk:** users see "Conflict" instead of the reason. **Fix:** convert handler `HTTPException`s to `APIProblem` codes. *Skipped:* support load.

### SD-25 · Shutdown and concurrency · E · S
V `infra/stack/main.tf:645-646` (`--timeout-graceful-shutdown 8`); `infra/modules/cloud_run/main.tf:198-216` (no readiness probe, so the drain flag does nothing); live worker concurrency 4 vs ADR-0130's 1 (`gcloud run services list`; `infra/stack/main.tf:124-127`). **Risk:** every deploy or liveness kill cuts up to 4 slices. **Fix:** worker concurrency 1; graceful shutdown ≥ 30 s with a cooperative checkpoint on SIGTERM; correct the docstring. *Skipped:* avoidable resumes and lost model calls.

### SD-26 · Breaker state per instance; no breaker on model calls · G · S
V `src/account_intelligence/connectors/factory.py:172-173`; `connectors/breaker.py:42`; NF breaker in `platform/llm.py`, `src/llm/`. **Risk:** each instance relearns an outage; model outages burn retries. **Fix:** keep breaker state in Redis (already present); add a breaker on model calls. *Skipped:* slower failure handling.

### SD-27 · Correlation and tracing gaps · L · S
V request id not propagated into Cloud Tasks (`adapters.py:139-146`); `trace_id` logged as a plain field, not `logging.googleapis.com/trace` (`src/obs/logging.py:81`); 5xx alert covers web and api only (`infra/modules/observability/platform.tf:44`). **Risk:** a user's failed run cannot be followed from browser to worker. **Fix:** pass `traceparent` and `x-request-id` as task headers; write the Cloud Logging trace field; include the worker in the 5xx alert. *Skipped:* slow incident diagnosis.

### SD-28 · Two run engines and unwired code · E · M
V Workflow v1 still reachable via `POST /v1/runs` when a tenant's `abm_agents` rule is off (`agents/production/wiring.py:341-378`; `routes/runs.py:116-197`); unreached: 11 of 14 refresh modules, signal intake, alert engine/outbox/digest, `governance.throttle`/`account_cost`, `evalgate` (static import closure from `api/main.py`); `routes/spend.py` and `spend_v2.py` both mounted; dead `api/idempotency.py`, `pagination.py`, `etag.py`. **Risk:** two code paths to secure and test; reviewers misread what runs. **Fix:** remove Workflow v1 after migrating any tenant rule; move unwired packages out of `src/` until they are wired; delete dead modules. *Skipped:* slower changes, larger attack surface.

### SD-29 · Unbounded table growth · F · M
V nothing deletes `checkpoints*`, `tool_calls`, `tool_call_events`, `llm_calls` (≤256 KiB `output_json` per row), `model_reply_log`, `audit_events`, `usage_events` except account purge (grep `DELETE FROM` in `src/`). **Risk:** a 20 GB database fills; vacuum and backups slow. **Fix:** prune checkpoints after terminal + 7 days; move payload columns to GCS; partition events by month. *Skipped:* storage and performance decay.

### SD-30 · Cloud project hygiene · K · S
V `my-service` (ingress `all`, default compute SA with `roles/editor`, no invokers) (`gcloud run services list`, `get-iam-policy`); `default` VPC with SSH/RDP open to 0.0.0.0/0 (`gcloud compute firewall-rules list`); default compute and Cloud Services SAs hold `roles/editor` (`gcloud projects get-iam-policy`); `smtp-password` secret after mail removal (`gcloud secrets list`); ops jobs with a client domain in args (`gcloud run jobs list`); `staging.plan` and tfvars with personal data on a laptop (V local files, git-ignored). **Fix:** delete `my-service` and the default VPC, remove Editor from default SAs (org policy `iam.automaticIamGrantsForDefaultServiceAccounts`), delete the orphan secret, parameterise ops jobs. *Skipped:* findings in every security review.

### SD-31 · Object erase is fire-and-forget · F · S
V account delete starts `_erase_stored` as an in-process task after responding on the CPU-throttled api (`api/routes/accounts.py:191-235,286-290`; `gcloud run services list` cpu-throttling true); failures are only logged. **Fix:** enqueue erase as a Cloud Task with retries and record completion. *Skipped:* orphaned personal data in GCS.

### SD-32 · Upload limits · B/I · S
V 20 GiB per file, no count or storage quota (`config/research_input_modes.yaml:7-14`); operator library upload accepts 100 MB through two Cloud Run HTTP/1 hops and buffers it in memory (`api/routes/library.py:63,134-136`; Cloud Run's 32 MiB HTTP/1 request limit, I). **Fix:** per-tenant storage quota; library upload via a resumable session like tenant uploads. *Skipped:* cost abuse; failing library uploads.

### SD-33 · Long-lived requests hold scarce slots · I · M
V Ask holds a web and an api request slot for up to 3600 s; run pages hold an SSE (60 s cap, DB poll every 1 s) plus 5 s polling (`config/views.yaml:27-37`; `api/routes/runs.py:781-790`); api is gen1 with 2 GiB because the embedding model loads in process (`infra/stack/main.tf:242`). Ceiling ≈120 concurrent requests per tier (I). **Fix:** raise api/web max instances; move run progress to a push channel fed by Postgres `LISTEN/NOTIFY` or longer poll intervals; move embedding for Ask queries to the worker or a small dedicated service. *Skipped:* UI stalls under modest load.

### SD-34 · Lock-order deadlock between Stop and settlement · F · S
V Stop locks `research_jobs` then `account_runs` (`research.py:338-372`); worker finish locks `account_runs` then `research_jobs` (`lifecycle.py:946`; `credits.py:400`); Stop catches only 55P03/57014 (`research.py:396-415`). **Risk:** occasional 500 on Stop (no money corruption). **Fix:** lock in one order. *Skipped:* flaky Stop.

### SD-35 · Scanning disabled in the project · K · S
V Artifact Registry `SCANNING_DISABLED` (containerscanning API not enabled); Error Reporting not enabled; CI's Trivy, pip-audit, npm audit and gitleaks (`.github/workflows/ci.yml:224-283`) do not gate laptop releases (SD-09). **Fix:** enable container scanning and Error Reporting; gate releases on CI. *Skipped:* vulnerable images in production.

### SD-36 · Model ids are aliases · H · S
V `config/models.yaml:210-300` (bare ids such as `claude-opus-5-5`, `kimi-k3:cloud`); `config/routing.yaml:5-11` contradicts `config/providers.yaml`. **Risk:** behaviour changes when a provider moves an alias; evals stop being comparable. **Fix:** pin dated snapshot ids where providers offer them and record `message.model` per call (already recorded, `platform/llm.py:601`); delete or regenerate `routing.yaml`. *Skipped:* unexplained quality shifts.

### SD-37 · Docs disagree with code · M · S
V 17 disagreements listed in `01-as-built.md` section 11. **Fix:** correct each; generate architecture facts (queue settings, ticks, routes) from code and Terraform in CI as the route catalog already is. *Skipped:* the team builds on wrong beliefs.

### SD-38 · Rendering and sandbox gaps · K · S
V LibreOffice recalc runs with a cleared env and timeout but no seccomp or network isolation (`src/parity/recalc.py:58-63`; `src/exports/verify.py:209-220`); the seccomp TSYNC path under Cloud Run is marked unverified (`src/documents/sandbox_child.py:28`). **Fix:** run `soffice` inside the same sandbox child; add a startup self-test that proves the seccomp filter installed and fail closed if not. *Skipped:* a crafted workbook in the exact-copy import path could reach an unsandboxed parser (I).
