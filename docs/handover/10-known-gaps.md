# 10 Known gaps

Verified against the code on 2026-11-03.

See also: [README](README.md) · [01 Architecture and planes](01-architecture-and-planes.md) · [02 Request path and auth](02-request-path-and-auth.md) · [03 Research run lifecycle](03-research-run-lifecycle.md) · [04 Model routing](04-model-routing.md) · [05 Spend and caps](05-spend-and-caps.md) · [06 Data and isolation](06-data-and-isolation.md) · [07 Documents and sandbox](07-documents-and-sandbox.md) · [08 Exports and gate](08-exports-and-gate.md) · [09 Ops](09-ops.md)

Each item below is true in the code today. Items that `docs/open-items.md` or `docs/decisions/open-decisions.md` list but the code has already closed are under "Code vs docs".

## 1. Data retention is not enforced in the database

- No retention job exists. The scheduler entries `retention` and `alert-digest` were removed because their routes were never built (`infra/stack/main.tf:801-804`). `src/account_intelligence/api/routes/tasks.py` has no `/tasks/retention` route.
- The sweeper says so explicitly: "data retention is not enforced in staging" (`src/account_intelligence/runs/sweeper.py:32-33`).
- What *is* enforced, at the storage layer only:
  - bucket lifecycle rules delete raw collection files `tenants/**/*.raw.json(.gz)` after `raw_retention_days`, quarantine after 7 days and `uploads/` after 3 days (`infra/modules/gcs/main.tf:98-124`); staging sets `raw_retention_days = 30` (`infra/envs/staging/terraform.tfvars:37`);
  - Cloud SQL backups are kept 7 days in staging and 30 in prod (`infra/stack/main.tf:463`).
- Postgres rows (facts, passages, deliverables, chat, link archive) are kept until a user deletes them or an erasure request runs.
- The `src/privacy` package was imported by nothing outside itself and was parked to `unwanted-things/dead-code-2026-11/`; the retention windows moved to `docs/registers/retention.yaml`, a register nothing enforces (ADR-0136).

## 2. Audit-archive retention lock: owner decision pending

- Staging `archive_lock` defaults to `true` (`infra/envs/staging/main.tf:102-105`), and the git-ignored `terraform.tfvars` does not override it. The next `terraform apply` therefore sets `retention_policy.is_locked = true` on the audit-archive bucket (`infra/modules/gcs/main.tf:172`).
- ADR-0125 (d): this **can never be undone**. The 7-year policy cannot be shortened, the bucket cannot be deleted, and the project cannot be deleted while it holds objects. The owner must apply it deliberately, or set `archive_lock = false` first (`docs/decisions/ADR-0125-secrets-iam-checkpointer-redis-tls.md:56-65`).
- Prod defaults to `false` (`infra/envs/prod/main.tf:83-86`).
- There is no lock on the **link archive** (`link_archive`). It is an insert-only index of captured copies (`src/account_intelligence/archive.py:1-13`). Profile copies are internal only (`src/account_intelligence/archive.py:54`), and a person's erasure deletes their profile rows (`src/account_intelligence/archive.py:615`).

## 3. The monitoring product is built but not wired (ADR-0134)

- `src/refresh`, `src/signals` and `src/alerts` are built and tested, but no scheduler tick, task route or worker job reaches them. Each package docstring starts with "NOT WIRED" (`docs/decisions/ADR-0134-monitoring-product-not-wired.md`, `src/refresh`, `src/signals`, `src/alerts`).
- The only way in is the 9 routes in `src/account_intelligence/api/routes/signals.py`. Each is decorated `@behind_flag("monitoring")`, and they answer 404 unless the client's `monitoring` flag is on (`src/account_intelligence/api/routes/signals.py`, `src/account_intelligence/api/flags.py:1-9`, `src/account_intelligence/api/flags.py:35`).
- Outside importers: only `routes/signals.py` and `crm_writeback.payloads`. `tests/guardrails/test_monitoring_not_wired.py` enforces this (`docs/decisions/ADR-0134-monitoring-product-not-wired.md`).
- None of the scheduler entries targets them (`infra/stack/main.tf:805-814`).
- The alert `refresh_scheduler_stalled` is paused for the same reason (`config/telemetry/alerts.yaml:26`).
- Still missing (`docs/open-items.md:140-146`):
  - the probe's grading port runs on fakes (OI-P4-1);
  - monitor, Slack, email and CRM adapters raise `NotConfigured` (OI-P4-5);
  - webhooks are not handed to the extractor (OI-P4-4).
- Keep the `monitoring` flag off for every client until this is wired.

## 4. Workflow v1 is deprecated, not yet removed (ADR-0135)

- `uses_abm` sends to the ABM graph: `pull`/`build` runs, research-job runs and linked file-enrichment runs always, and other `full` runs when `abm_flag_on` (`src/agents/production/wiring.py`, `uses_abm` and `abm_flag_on`).
- `abm_flag_on` returns **true when no `abm_agents` rule was ever written**. An explicit rule still decides through `admin.flags.evaluate`, so a rule listing no tenant, plan or percentage sends `full` runs back to v1 (`src/agents/production/wiring.py`, module docstring and `abm_flag_on`).
- Every other flag is deny-by-default (`src/admin/flags.py:24-28`).
- When a run reaches v1, `_slice` logs `workflow_v1.deprecated` on slice 0. The comment says "removal is the release after next" (`src/account_intelligence/workflow/runner.py`, `_slice`).
- ADR-0135 records the decision. Removal (parking) of v1 is set for the release after next; until then v1 stays whole, and tests that pin v1 use the `pin_workflow_v1` fixture (`docs/decisions/ADR-0135-workflow-v1-deprecated.md`).
- This change landed on 2026-11-03 while this page was being written. Before release, confirm that no tenant has an explicit `abm_agents` rule you did not intend.

## 5. Other gaps still true in code

- **Service-role split not live in the cloud.** Migration 0101 and the code are done. ADR-0123 marks the Terraform part "planned, not applied" (`docs/decisions/ADR-0123-split-runtime-database-roles.md:3`), so the services keep running as `app_rw` and log `db.role_pending` (`src/account_intelligence/db/session.py:142-149`). `app_sweeper` has no user (`migrations/0101_service_roles.sql:15-18`). See [06](06-data-and-isolation.md).
- **`tenant_txn(..., system=True)` is open to every module.** Only `db/system_scope.py` is allow-listed; ADR-0123 names this as the next thing to narrow (`docs/decisions/ADR-0123-split-runtime-database-roles.md`).
- **Parser sandbox residual risk.** Hijacked native parser code can read files uid 10001 can read, including the bucket mount. The separate parse job is proposed, not built (`docs/decisions/ADR-0124-parser-sandbox-untrusted.md:3`, `docs/decisions/ADR-0124-parser-sandbox-untrusted.md:41-47`).
- **Alerts reach nobody.** Staging `alert_channels = []` (`infra/envs/staging/terraform.tfvars:32`), and that list feeds every page and ticket channel (`infra/stack/main.tf:833-834`).
- **Most workload alert thresholds are proposals** (`config/telemetry/alerts.yaml:1-5`).
- **The Node Word renderer for exports is not ported.** `EXPORT_RENDERER=node` gives `NotConfigured`, because `src/exports/word/node/render.js` does not exist. Production uses python-docx (`src/exports/word/renderer.py:62-69`, `infra/stack/main.tf:343`).
- **Large uncommitted tree.** The last committed migration is 0097. 0098 to 0102 and many source files (ADR-0106 to ADR-0135) exist only in the working tree (`git ls-files migrations/versions`). The workspace-zip import (`src/documents/workspace.py`, migration 0102) is still in progress in another session.

## Code vs docs

- Earlier notes speak of a "link-archive lock pending owner". No such lock exists in `src/account_intelligence/archive*` or `docs/decisions`. The pending irreversible lock is the **audit-archive** retention lock (section 2).
- ADR-0135 matches the code as of the end of 2026-11-03. Earlier the same day `uses_abm` was deny-by-default, so check the version you deploy.
- `docs/decisions/open-decisions.md` D-97 says the owner "must approve the consent text" (`docs/decisions/open-decisions.md:75`). The text is approved as version `pdc-1` (`config/skill/consent.yaml:2`, `config/skill/consent.yaml:9`), and `consent_decision` gates people pulls on it (`src/agents/guards.py:250-265`).
- `docs/open-items.md` OI-BLD-1 ("No deliverable builder or production quality gate exists"), V-EXP-2 ("the production image has no LibreOffice"), OI-IM-5 (clamd needed) and V-REGION-1 (asia-south1) are out of date (`docs/open-items.md:51`, `docs/open-items.md:25`, `docs/open-items.md:173`, `docs/open-items.md:9`). See [07](07-documents-and-sandbox.md), [08](08-exports-and-gate.md), [09](09-ops.md).
