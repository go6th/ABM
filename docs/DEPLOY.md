# Production Deployment Guide: Google Cloud Platform (GCP)

This document provides operational instructions for deploying the ABM Generator stack to Google Cloud Platform.
No live cloud mutations are executed automatically; deployment is gated by explicit owner authorization and WIF pipelines.

---

## 1. Required Owner Inputs Before Deployment

Before deploying staging or production environments, the infrastructure owner must provide:

1. **GCP Project ID**: Staging and production project identifiers (e.g., `ai-abm-staging-123`, `ai-abm-prod-456`).
2. **Deployment Region**: Selected Google Cloud region (e.g., `us-central1` for US-only inference compliance).
3. **Billing Authorization**: Confirmation that Google Cloud billing is active and attached to the target projects.
4. **Production Domain**: The public DNS domain for the external HTTPS load balancer (e.g., `app.example.com`).

Do not hardcode or place secrets in repository configuration, Terraform variables, or Git commits.

---

## 2. Infrastructure Architecture Overview

The production architecture is provisioned via modular Terraform (`infra/`):

- **Compute & Ingress**:
  - `web` (Next.js 16 standalone): Cloud Run v2 service behind an External HTTPS Load Balancer with Google-managed SSL and Cloud Armor WAF rules (`infra/modules/edge/`, `infra/modules/cloud_run/`).
  - `api` (FastAPI): Cloud Run v2 service with Direct VPC egress, accessible internally from `web` and Cloud Tasks.
  - `worker` (Cloud Run Job): One-run-per-container execution driven by Cloud Tasks (`infra/modules/cloud_run_jobs/`).
  - `migrate` (Cloud Run Job): Runs `alembic upgrade head` before service rollout.
- **Data & Storage**:
  - Cloud SQL PostgreSQL 16 with `pgvector`, Private IP only, CMEK encryption, and IAM database authentication (`infra/modules/cloud_sql/`).
  - Cloud Storage: Private CMEK-encrypted bucket for tenant payloads under `tenants/{tenant}/runs/{run_id}/raw/` (`infra/modules/gcs/`).
  - Memorystore Redis: Shared rate limiting and concurrency governance (`infra/modules/cache/`).
- **Identity & Security**:
  - Google Secret Manager: Holds provider credentials and runtime secrets referenced via `secret_key_ref` (`infra/modules/secret_manager/`).
  - Cloud KMS: CMEK for storage, SQL, and envelope encryption for tenant BYOK keys (`tenant_model_credentials`).
  - Google Cloud Identity Platform: Multi-tenant OIDC discovery and token verification (`infra/modules/identity_platform/`).
  - IAM: Least-privilege service accounts (`api`, `job`, `web`, `migrate`, `deployer`) with GitHub Workload Identity Federation (`infra/modules/iam/`).
- **Governance & Monitoring**:
  - Cloud Billing Budget: Monthly spend caps and alert notifications (`infra/modules/budget/`).
  - Cloud Scheduler: Triggers nightly spend invariant jobs (`infra/modules/scheduler/`).

---

## 3. Required Service Account Permissions

| Service Account | Role / Permissions | Purpose |
|---|---|---|
| `api` | `roles/cloudsql.client`, `roles/secretmanager.secretAccessor` (scoped), `roles/cloudtasks.enqueuer`, `roles/storage.objectUser` | Serves API traffic and orchestrates runs. |
| `job` (worker) | `roles/cloudsql.client`, `roles/secretmanager.secretAccessor` (scoped), `roles/storage.objectUser` | Executes isolated account research runs. |
| `web` | `roles/run.invoker` (internal api service) | Proxies client traffic server-side. |
| `migrate` | `roles/cloudsql.client`, `roles/secretmanager.secretAccessor` (`app_rw_password`, `migrator_url`) | Executes schema migrations. |
| `deployer` (WIF) | `roles/run.developer`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser` | GitHub Actions CI/CD deployment runner. |

---

## 4. Pre-Deployment Verification (Local)

Run local diagnostics and Terraform syntax checks:

```bash
# 1. Inspect local developer environment and stack health
make doctor

# 2. Verify Terraform formatting and validation across staging and prod
make infra-fmt
make infra-validate

# 3. Verify environment registry schema
make env-check
```

---

## 5. Step-by-Step Deployment Procedure

### Step 1: Terraform Initialization & Planning

```bash
cd infra/envs/staging # or infra/envs/prod

# 1. Initialize with remote GCS state bucket
terraform init -backend-config="bucket=${TF_STATE_BUCKET}"

# 2. Generate and review deployment plan
terraform plan -var-file="terraform.tfvars" -out="staging.tfplan"

# 3. Apply infrastructure resources (requires human review)
terraform apply "staging.tfplan"
```

### Step 2: Secret Population in Secret Manager

Add required secret versions out-of-band (never committed to Git):

```bash
# Model provider key
gcloud secrets versions add anthropic-api-key --data-file=<(printf "%s" "$ANTHROPIC_API_KEY")

# Provider connector keys
gcloud secrets versions add p-search-api-key --data-file=<(printf "%s" "$P_SEARCH_API_KEY")
gcloud secrets versions add p-scrape-api-key --data-file=<(printf "%s" "$P_SCRAPE_API_KEY")
gcloud secrets versions add p-signals-api-key --data-file=<(printf "%s" "$P_SIGNALS_API_KEY")
gcloud secrets versions add p-signals-api-token --data-file=<(printf "%s" "$P_SIGNALS_API_TOKEN")
gcloud secrets versions add p-people-api-key --data-file=<(printf "%s" "$P_PEOPLE_API_KEY")
gcloud secrets versions add p-org-api-key --data-file=<(printf "%s" "$P_ORG_API_KEY")
```

### Step 3: Container Build, Scan, Sign, and Rollout

Executed only by GitHub Actions (`.github/workflows/deploy-staging.yml` or `deploy-prod.yml`), which deploy the built images by digest; there is no manual deploy script (see `docs/deploy/RUNBOOK.md`).

The workflow:
1. Builds `api`, `worker`, and `web` containers.
2. Runs Trivy vulnerability scans (fails on HIGH or CRITICAL).
3. Generates and uploads CycloneDX SBOMs.
4. Pushes image digests to Artifact Registry.
5. Signs image digests keylessly with Cosign (Sigstore OIDC).
6. Executes Cloud Run migration job (`alembic upgrade head`).
7. Updates Cloud Run services and jobs with zero downtime.

---

## 6. Rollback Runbook

If an issue occurs after deployment:

1. **Traffic Rollback**:
   ```bash
   gcloud run services update-traffic ${CLOUD_RUN_API_SERVICE} --to-revisions=${PREVIOUS_REVISION}=100 --region=${GCP_REGION}
   gcloud run services update-traffic ${CLOUD_RUN_WEB_SERVICE} --to-revisions=${PREVIOUS_REVISION}=100 --region=${GCP_REGION}
   ```
2. **Database Schema Rollback**:
   Check migration reversibility before rolling back. For irreversible data changes, deploy a forward-fix migration.
