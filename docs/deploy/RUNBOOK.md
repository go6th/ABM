# Runbook: Google Cloud staging and production

For the owner and the on-call engineer. The architecture and sizing are in
`docs/deploy/ARCHITECTURE.md`. **Terraform (`infra/`) plus the GitHub deploy workflows
(`.github/workflows/deploy-staging.yml`, `deploy-prod.yml`) are the only deploy path**; nothing
else creates or changes cloud resources. Every command below needs the owner's own Google Cloud
account; nothing here has been run by the build, and nothing is run until the owner says "deploy"
with a project id.

Placeholders: `ENV` is `staging` or `prod`; `PROJECT` is that environment's project id; `REGION`
its region; `DOMAIN` its web hostname; names follow `ai-ENV-*` (for example `ai-prod-worker`).

## 1. One-time setup (owner)

### 1.1 Projects, billing, state bucket

1. Create one project per environment and link billing (console, or
   `gcloud projects create PROJECT` and `gcloud billing projects link PROJECT --billing-account=ID`).
2. Choose the region (`us-central1` or `europe-west2`, see the cost table in ARCHITECTURE.md
   section 6) and check VERIFY(V-REGION-1): Cloud Run, Cloud SQL, Memorystore, Cloud Tasks and,
   with `llm_access = "vertex_ai"`, the Claude models on Vertex AI must exist there.
3. Create the Terraform state bucket once (it holds both environments' state under prefixes):
   `gcloud storage buckets create gs://STATE_BUCKET --project=ADMIN_PROJECT --location=REGION --uniform-bucket-level-access --public-access-prevention`
   then `gcloud storage buckets update gs://STATE_BUCKET --versioning`. Only the owner (and later
   the deployer, if CI runs Terraform) may read it: state contains resource names and the
   Memorystore CA, never a secret value.
4. Notification channels: create the e-mail, chat or paging channels in Cloud Monitoring and note
   their ids (`projects/PROJECT/notificationChannels/ID`) for `alert_channels`.

### 1.2 Variables

`cd infra/envs/ENV && cp terraform.tfvars.example terraform.tfvars` (the file is ignored by git)
and fill in:

| Variable | What |
| --- | --- |
| `project_id`, `region`, `billing_account`, `domain` | the environment's project, region, billing account id, web hostname |
| `github_repository` | `OWNER/REPO` allowed to deploy (WIF) |
| `images` | first image digests (build once by hand or let the first CI run push them, then fill in) |
| `clamav_image` | ClamAV mirrored into the registry and pinned by digest (1.3) |
| `db_tier` | `db-custom-1-3840` (staging), `db-custom-2-7680` (production) proposed |
| `llm_access` | `anthropic_api` (key in Secret Manager) or `vertex_ai` |
| `monthly_budget_usd` | the budget amount; alerts at 50, 90 and 100 percent |
| `alert_channels` | notification channel ids |
| `raw_retention_days` | days raw collection files are kept (owner and counsel decision) |
| `staff` | Workspace domain, operator and support allow-lists, the staff OAuth client id (1.5) |
| optional | `db_availability` (prod default `REGIONAL` = high availability), `db_max_connections`, `redis_tier`, `scaling`, `moonshot_enabled`, `maildrive`, `archive_lock`, `workload_alerts_enabled` |

### 1.3 First apply

```
cd infra/envs/ENV
terraform init -backend-config="bucket=STATE_BUCKET" -backend-config="prefix=ENV"
terraform plan -out=ENV.tfplan          # read it: about 195 resources to add on a new project
terraform apply ENV.tfplan
```

Then:
1. DNS: an A record for `DOMAIN` to the output `web_ip`. The managed certificate provisions once
   DNS resolves (up to an hour).
2. ClamAV image: `docker pull clamav/clamav:stable`, tag and push it to the output `registry`
   (`.../clamav`), put its digest in `clamav_image`, and apply again. Re-mirror monthly.
3. Providers that allow-list callers get the output `nat_ip`.
4. GitHub: in the repository's `staging` / `production` environment (required reviewers on
   production) set the variables `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER` (output
   `wif_provider`), `DEPLOYER_SERVICE_ACCOUNT` (output `deployer_email`), `ARTIFACT_REGISTRY`
   (output `registry`), `CLOUD_RUN_API_SERVICE`, `CLOUD_RUN_WEB_SERVICE`,
   `CLOUD_RUN_WORKER_SERVICE` (outputs `services`), `CLOUD_RUN_MIGRATE_JOB` (output `jobs`). No
   GitHub secret is needed for Google Cloud (WIF).

### 1.4 Secrets and their values (names only; values never in git, chat or Terraform)

Add each value as a new version, from a file or stdin so it stays out of shell history:
`gcloud secrets versions add NAME --project=PROJECT --data-file=-` (paste, then Ctrl-D).
A Cloud Run revision does not start while a secret it references has no version, so add all
required ones before the first deploy.

| Secret id | Read by | Value | Required |
| --- | --- | --- | --- |
| `checkpoint-database-url` | api, worker, nightly job | `postgresql://app_rw:APP_RW_PASSWORD@SQL_PRIVATE_IP:5432/ai?sslmode=require` | yes |
| `migrator-database-url` | migrate job | `postgresql://migrator:PASSWORD@SQL_PRIVATE_IP:5432/ai?sslmode=require` | yes |
| `app-rw-password` | migrate job | the password migrations set on `app_rw` (same as in the checkpoint URL) | yes |
| `csrf-signing-key` | api | 32 or more random bytes, e.g. `openssl rand -base64 48` | yes |
| `operator-google-client-secret` | web | staff console OAuth client secret (1.5) | yes |
| `anthropic-api-key` | api, worker | the model API key | with `llm_access = anthropic_api` |
| `p-search-api-key`, `p-scrape-api-key`, `p-signals-api-key`, `p-signals-api-token`, `p-people-api-key`, `p-org-api-key` | worker | provider keys (docs/runbooks/provider-credentials.md) | yes (a placeholder value leaves the provider "unconfigured") |
| `moonshot-api-key` | api, worker | Kimi K3 key | only before setting `moonshot_enabled = true` |
| `maildrive-google-client-secret`, `maildrive-microsoft-client-secret` | api, worker | Mail and drive OAuth client secrets (1.5) | created only when the matching client id is set in `maildrive` |
| `redis-server-ca` | api, worker | written by Terraform (the Memorystore CA certificate is public) | automatic |

The SQL private IP is shown by `gcloud sql instances describe ai-ENV-pg --format='value(ipAddresses[0].ipAddress)'`.

### 1.5 OAuth apps

- **Staff console** (Google Workspace): in the Google Cloud console of the environment, an OAuth
  client of type "Web application", authorised redirect URI `https://DOMAIN/api/operator/callback`,
  consent screen internal to the Workspace. Client id into `staff.oauth_client_id`, secret into
  `operator-google-client-secret`.
- **Mail and drive** (optional): a Google OAuth client with redirect URI
  `https://DOMAIN/v1/mail-and-drive/callback` (Gmail and Drive read scopes; Google verification
  needed for external users), and a Microsoft Entra app with the same redirect URI. Ids into
  `maildrive`, secrets into the two `maildrive-*` secrets, then apply.
- **Tenant sign-in** uses Identity Platform (created by Terraform); per-client SSO connections
  are configured in the product.

#### Mail and drive go-live (Gmail, Google Drive, Outlook, OneDrive, SharePoint)

Per user, read-only; matched items are private to their owner (owner decision 2026-10-14).

1. **Domain.** Use a domain you own for `PUBLIC_BASE_URL` (https). Google does not verify apps on
   wildcard DNS such as `*.sslip.io`.
2. **Google.** In the environment's Google Cloud project: OAuth consent screen (External), scopes
   `gmail.readonly` and `drive.readonly`; an OAuth client of type "Web application" with redirect
   URI `https://DOMAIN/v1/mail-and-drive/callback`. Both scopes are *restricted*: until the app
   passes Google verification (including the CASA security assessment), only listed test users
   can connect and their refresh tokens expire after 7 days.
3. **Microsoft.** Entra ID app registration, "Accounts in any organizational directory and
   personal Microsoft accounts", Web redirect URI as above, delegated Graph permissions
   `Mail.Read`, `Files.Read`, `Sites.Read.All` and `offline_access`; a client secret (note its
   expiry date). `Sites.Read.All` needs admin consent in most customer tenants.
4. **Configure.** The guided way: `uv run --frozen python scripts/maildrive_setup.py configure
   --env staging` (domain and ids into terraform.tfvars, prints the DNS record and redirect URI),
   deploy, then `... secrets --env staging` (hidden input, straight into Secret Manager) and
   `... check --env staging` (must print READY). Google verification material:
   docs/deploy/google-oauth-verification.md. By hand: client ids into the `maildrive` Terraform variable
   (`google_client_id`, `microsoft_client_id`), secrets into `maildrive-google-client-secret` and
   `maildrive-microsoft-client-secret`, then apply. The `maildrive-sync` Cloud Scheduler job runs
   hourly (`5 * * * *`).
5. **Check before telling anyone.** From an operator machine with the same environment:
   `uv run --frozen python scripts/maildrive_selfcheck.py --cloud --network`, then with a test
   user's session cookie `--api https://DOMAIN --cookie "<cookie>"`. It must print READY.
6. **Smoke test.** As a test user: Mail and drive → Connect Gmail → consent → back on the page as
   Connected → Sync now → "items matched" appears for an account whose domain you emailed →
   Ask about that account cites the email → Disconnect (asks first) → the items are gone.

### 1.6 Database users (once per environment)

Connect as the built-in `postgres` user through Cloud SQL Studio or the Cloud SQL Auth Proxy from
an operator machine (set its password first in the console; keep it in your password manager).

1. `CREATE USER migrator WITH PASSWORD '...' CREATEROLE;` then `GRANT ALL ON DATABASE ai TO migrator;`
   and `ALTER DATABASE ai OWNER TO migrator;`. On Cloud SQL this user is **not a superuser**
   (member of `cloudsqlsuperuser`: it can create roles and the `vector` extension; it has no
   BYPASSRLS). Put its URL into `migrator-database-url`.
2. Run the migrate job once (2.2 step 3). Migrations create `app_rw` with the password from
   `app-rw-password`.
3. Grant the runtime IAM users `app_rw` and nothing else (until a migration does it from
   `APP_RW_IAM_USERS`):
   ```
   GRANT app_rw TO "ai-ENV-api@PROJECT.iam";
   GRANT app_rw TO "ai-ENV-worker@PROJECT.iam";
   GRANT app_rw TO "ai-ENV-job@PROJECT.iam";
   ```
   VERIFY(V-SQL-1): IAM login over the private IP works only on the real instance.
4. Run the database checks of section 6.

## 2. Deploy

### 2.1 Infrastructure changes

`terraform plan -out=ENV.tfplan`, read every change (anything marked "must be replaced" on the
database, bucket or KMS key is a stop: deletion protection will refuse it anyway), then
`terraform apply ENV.tfplan`. Staging first, production after staging checks pass.

### 2.2 Application release (the normal path)

Merge to `main`: CI runs every gate; `deploy-staging` (after the environment's reviewers approve)
then:
1. builds the api, worker and web images, scans them (Trivy, fails on HIGH or CRITICAL), writes
   SBOMs, pushes by digest and signs them (cosign, keyless);
2. updates the `migrate` job to the new api image digest;
3. executes `migrate` and waits; **a failed migration stops the release** (and pages);
4. rolls the new digests onto `ai-ENV-api`, `ai-ENV-worker` and `ai-ENV-web`;
5. checks that each service's latest revision is ready.

Production: run `deploy-prod` by hand from the same commit after staging's checks (section 6)
pass. Migrations are expand-only, so the previous revision keeps working on the new schema.

## 3. Rollback

### 3.1 To the previous revision (minutes; no data change)

```
gcloud run revisions list --service=ai-ENV-SERVICE --region=REGION --project=PROJECT
gcloud run services update-traffic ai-ENV-SERVICE --to-revisions=PREVIOUS_REVISION=100 --region=REGION --project=PROJECT
```

Do it for web, api and worker together when the release touched all three. Terraform ignores
traffic and image, so a later `terraform apply` does not undo a rollback; the next CI deploy
sends traffic to the new revision again. Expand-only migrations mean the database needs no change.

### 3.2 To a database restore point (data loss or corruption)

Point-in-time recovery restores **into a new instance**; the old one is kept for the post-mortem.

1. Pick the time just before the incident (audit log, Cloud Logging).
2. Stop writes: scale the worker to zero and pause the queues
   (`gcloud tasks queues pause ai-ENV-runs` and the same for `documents` and `jobs`), pause the
   Scheduler ticks.
3. Clone: `gcloud sql instances clone ai-ENV-pg ai-ENV-pg-restore-YYYYMMDD --point-in-time='2026-10-14T09:00:00Z'`.
4. Run section 6's database checks against the clone (RLS catalog, app_rw members, the invariant
   rehearsal of docs/runbooks/restore.md and the audit hash-chain verification).
5. Switch: either import the clone into Terraform (rename `name` in `modules/cloud_sql` to the
   clone's name with an `import` block, the owner's change), or restore a backup in place
   (3.3). Update `checkpoint-database-url` and `migrator-database-url` with the new private IP,
   apply, and redeploy so new revisions read them.
6. Resume queues and ticks, scale the worker back.

Practise this on staging before relying on it (RTO target 30 minutes, RPO under a minute:
targets, unproven until a game day).

## 4. Rotating secrets

1. Add a new version (1.4). For keys that must overlap (provider keys, OAuth client secrets),
   create the new key at the provider first.
2. Redeploy the services that read it so new instances pick up `latest`: with env-injected
   secrets, `gcloud run services update ai-ENV-SERVICE --region=REGION --update-labels=rotated=YYYYMMDD`
   creates a new revision; secrets read through the Secret Manager API (`anthropic-api-key`,
   `moonshot-api-key`, `csrf-signing-key`) are cached per instance, so the same new revision
   picks them up.
3. Check the service's `/readyz` (section 6), then disable the old version
   (`gcloud secrets versions disable N --secret=NAME`) and destroy it after a week.

Specific cases:
- `csrf-signing-key`: rotating it invalidates CSRF tokens of open sessions; users reload.
- `app-rw-password` and `checkpoint-database-url`: change both together: new version of
  `app-rw-password`, run the migrate job (it sets the password), new `checkpoint-database-url`,
  redeploy api and worker.
- `migrator-database-url`: `ALTER USER migrator PASSWORD '...'` as `postgres`, then a new
  version; only the migrate job reads it.
- The KMS keys rotate themselves every 90 days. docs/runbooks/secrets.md and
  docs/runbooks/rotation_failure.md hold the older notes.

## 5. Restoring a backup

- Automated backups: daily, 7 kept on staging and 30 on production; point-in-time recovery with
  7 days of logs. List: `gcloud sql backups list --instance=ai-ENV-pg`.
- Restore a backup into a **new** instance first (`gcloud sql instances clone` for a point in
  time, or create an instance and `gcloud sql backups restore BACKUP_ID --restore-instance=NEW
  --backup-instance=ai-ENV-pg`), check it (section 6), then follow 3.2 step 5.
- Restoring over the live instance is possible but destroys its current data: only with the
  owner's explicit approval, after stopping writes.
- Objects: a deleted or overwritten object stays recoverable for 7 days as a noncurrent version
  and 7 more days soft deleted (`gcloud storage objects list --soft-deleted`,
  `gcloud storage restore`).

## 6. Checks after each deploy

Run on staging after every release, and on production after its release.

1. **Revisions ready**: the deploy's own check passed; `gcloud run services describe` shows the
   latest revision ready for web, api and worker.
2. **Health**: `curl -fsS https://DOMAIN/healthz` (web) and `curl -fsS https://DOMAIN/readyz`
   (web asks the api; the JSON names any failed component). Uptime checks green in Monitoring.
3. **Migrations**: the migrate execution succeeded; then, as the migrator, the RLS catalog check
   returns no rows:
   `psql "$MIGRATOR_DATABASE_URL" -tA -f scripts/check_rls_catalog.sql`.
4. **Runtime database users (audit L16)**: as the migrator,
   ```
   SELECT r.rolname, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
          (SELECT count(*) FROM pg_class c WHERE c.relowner = r.oid)  AS owned_relations,
          (SELECT count(*) FROM pg_proc p WHERE p.proowner = r.oid)   AS owned_functions,
          (SELECT string_agg(g.rolname, ',' ORDER BY g.rolname)
             FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
            WHERE m.member = r.oid)                                   AS member_of
     FROM pg_roles r
    WHERE pg_has_role(r.rolname, 'app_rw', 'MEMBER') AND r.rolname <> 'app_rw'
    ORDER BY 1;
   ```
   Expect exactly the three IAM users (`ai-ENV-api@PROJECT.iam`, `...-worker@...`,
   `...-job@...`), each with `f` for super, bypassrls, createrole and createdb, 0 owned relations
   and functions, and `member_of` = `app_rw` plus only Cloud SQL's IAM marker role
   (`cloudsqliamserviceaccount`), never `cloudsqlsuperuser`. Also
   `SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_rw'` returns `f`.
5. **Tasks reach the worker (V-TASKS-1)**: start a hermetic-sized research job on a test client
   and watch `ai-ENV-runs` drain (`gcloud tasks queues describe`); the worker's logs show
   `/tasks/run-slice` answered 200, never 403. A 403 means the tasks identity, audience
   (`ai-ENV-worker-tasks`) or invoker grant is wrong. Check that a duplicate slice enqueue is
   refused by name.
6. **Uploads straight to storage (V-FILES-1)**: in the web app upload a 100 MB file, reload the
   page mid-upload and check it resumes from the committed offset; the browser's network panel
   shows chunk PUTs to `storage.googleapis.com` answered 308 with a `Range` header, then 200, and
   no CORS error; the document goes `quarantined` -> scanned -> `parsed`. Then upload the EICAR
   test string as a `.txt` file: it must end `rejected` (clamd is live).
7. **Redis over TLS (V-REDIS-1)**: `/readyz` shows `rate_limit_store: redis`; ten wrong sign-ins
   from one account are limited across instances. Then try `ssl_check_hostname=true` in
   `REDIS_URL` on staging; keep it if connections still succeed.
8. **Database over IAM (V-SQL-1)**: `/readyz` database component ok on api and worker; the
   Cloud SQL logs show the IAM users logging in, not `app_rw`, except the checkpointer.
9. **Scheduler**: `gcloud scheduler jobs list` shows the four live ticks `ENABLED` and the Phase 5
   ticks `PAUSED`; force one: `gcloud scheduler jobs run ai-ENV-schedules-tick` and check the
   worker answered 200. The next morning the isolation check has a passing row (console pill).
10. **Edge**: `curl -sI http://DOMAIN` answers 301 to https; repeated sign-in attempts from one
    address get 429 after 30 a minute; the Cloud Armor log shows no blocked legitimate requests
    (review in preview before raising sensitivity).
11. **Budget and alerts**: the budget exists with 50/90/100 percent thresholds; alert policies
    list the notification channels.

## 7. Phase 5 pieces that apply on deploy

Created by Terraform now, switched on later:
- Paused Scheduler ticks `sweep`, `retention`, `alert-digest`, `audit-verify`: when FIX_PROMPT
  Phase 5 ships the route, confirm the path in `infra/stack/main.tf` and set `paused = false`.
  Retention must stay paused until the owner confirms the deletion policy.
- Audit archive bucket `ai-ENV-audit-archive` (7-year retention, unlocked): set
  `archive_lock = true` only on the owner's decision; locking cannot be undone and the bucket
  cannot be deleted for 7 years.
- Application alerts (`workload_alerts_enabled`) once the OTLP exporter ships (V-OBS-1).

Existing incident runbooks: docs/runbooks/ (stuck-runs, stuck-scheduler, isolation-violation,
database, database-failover, restore, secrets, provider-credentials, api-availability-burn,
api-latency and the rest), referenced by the alerts.
