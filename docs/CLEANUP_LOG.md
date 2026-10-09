# Cleanup log

The following tracked legacy files were removed as part of the release cleanup. The replacement
paths are listed where applicable. Local `.env` and source documents remain outside this log and
are strictly preserved; `.claude/skills` is preserved.

## Tracked Files Removed and Replacements

| Deleted path | Category | Reason & Replacement |
| --- | --- | --- |
| `Dockerfile` | Obsolete | Replaced by service-specific `docker/api.Dockerfile`, `docker/worker.Dockerfile`, and `web/Dockerfile`. |
| `infra/envs/dev/.terraform.lock.hcl` | Obsolete | Removed with the obsolete dev Terraform environment. |
| `infra/envs/dev/main.tf` | Obsolete | Replaced by explicit staging and prod environment roots (`infra/envs/staging/main.tf`, `infra/envs/prod/main.tf`). |
| `infra/envs/dev/terraform.tfvars.example` | Obsolete | Removed with the obsolete dev Terraform environment. |
| `infra/envs/main.tf.tmpl` | Obsolete | Replaced by modular environment roots; template-generation workflow retired. |
| `infra/envs/production/.terraform.lock.hcl` | Obsolete | Replaced by `infra/envs/prod/.terraform.lock.hcl`. |
| `infra/envs/production/main.tf` | Obsolete | Replaced by `infra/envs/prod/main.tf`. |
| `infra/envs/production/terraform.tfvars.example` | Obsolete | Replaced by `infra/envs/prod/terraform.tfvars.example`. |
| `infra/gen_envs.sh` | Obsolete | Removed template-copy workflow; Terraform environments are maintained modularly. |
| `infra/modules/database/main.tf` | Obsolete | Replaced by decomposed module `infra/modules/cloud_sql/main.tf`. |
| `infra/modules/identity/main.tf` | Obsolete | Replaced by decomposed module `infra/modules/identity_platform/main.tf` and `infra/modules/iam/main.tf`. |
| `infra/modules/runtime/main.tf` | Obsolete | Replaced by `infra/modules/cloud_run/main.tf` and `infra/modules/cloud_run_jobs/main.tf`. |
| `infra/modules/secrets/main.tf` | Obsolete | Replaced by `infra/modules/secret_manager/main.tf`. |
| `infra/modules/storage/main.tf` | Obsolete | Replaced by `infra/modules/gcs/main.tf`. |
| `.env.example` | Obsolete | Removed after `docs/ENVIRONMENT.md` was generated and verified for all documented variable names, purposes, and secret classifications. `.env` was preserved. |

## Generated Caches and Build Artifacts

The following generated cache and temporary directories are excluded via `.gitignore` and safely purged by `make clean`:

| Path | Type | Purpose / Action |
| --- | --- | --- |
| `__pycache__/` | Generated | Python bytecode caches across all packages. |
| `.pytest_cache/` | Generated | pytest test session cache. |
| `.mypy_cache/` | Generated | Mypy type checker cache. |
| `.ruff_cache/` | Generated | Ruff linter cache. |
| `.hypothesis/` | Generated | Hypothesis property-based fuzz data. |
| `.import_linter_cache/` | Generated | Import linter contract cache. |
| `.coverage` | Generated | Coverage measurement database. |
| `htmlcov/` | Generated | HTML coverage reports. |
| `dist/`, `build/` | Generated | Python packaging build artifacts. |
| `web/.next/` | Generated | Next.js build cache. |
| `web/node_modules/.cache/` | Generated | Web bundler cache. |

## Preserved Assets (Strict Guarantee)

The following items are strictly preserved and never modified or deleted:
* `.env` (contains user-configured environment variables and provider keys)
* `.claude/skills` (user skills and configurations)
* All source documents and PRD assets (`docs/sources/`, `*.docx`)
* All database migrations (`migrations/` through `0046_tenant_model_credentials.py`)
* Active application code and runtime assets
