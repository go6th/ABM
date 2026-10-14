# Production Deployment & Operation Guide

This guide details the deployment procedures, configuration parameters, container setup, and verification routines for the Account Intelligence & ABM Platform.

---

## 1. Local & Staging Deployment (Docker Compose)

The entire application stack is containerized and managed via Docker Compose.

### Service Matrix

| Service | Port | Image | Purpose | Health Check |
| :--- | :--- | :--- | :--- | :--- |
| **`web`** | 3000 | `account-intelligence-web:local` | Next.js 16 Claude Workspace & Operator UI | `GET /` -> 200 OK |
| **`api`** | 8080 | `account-intelligence-api:local` | FastAPI Backend & Multi-Tenant Core | `GET /v1/health` -> 200 OK |
| **`worker`**| 8081 | `account-intelligence-worker:local` | Research Graph & Background Agent Engine | Uvicorn live |
| **`db`** | 5432 | `pgvector/pgvector:pg16` | PostgreSQL 16 with pgvector & RLS | `pg_isready -U postgres` |
| **`redis`** | 6379 | `redis:7-alpine` | Concurrency, Caching & Token Buckets | `redis-cli ping` |
| **`tasks`** | 8123 | `cloud-tasks-emulator:latest` | Cloud Tasks queue emulation | Emulated queue |
| **`fake-idp`**| 9090 | `account-intelligence-worker:local` | Local OIDC/JWKS Identity Provider | `GET /.well-known/jwks.json` |
| **`gcs`** | 4443 | `fake-gcs-server:latest` | Cloud Storage Emulator for raw artifacts | Storage ready |

### Quick Start Commands

```bash
# 1. Build and start all services in background
docker compose up -d --build

# 2. Verify all container statuses and health
docker compose ps

# 3. View live logs for web or api
docker compose logs -f web
docker compose logs -f api

# 4. Stop containers
docker compose down
```

---

## 2. Cloud Deployment (Google Cloud Platform)

There is one deploy path: Terraform for the infrastructure and the GitHub Actions workflows for
releases. CI builds the images once, checks and runs them hermetically, and the deploy workflow
pushes those exact images, runs the migration job, deploys by digest, smoke tests and rolls back
on failure. Production needs the owner's approval in the `production` environment. Nothing is
deployed until the owner says so.

- Architecture, sizes and estimated cost: `docs/deploy/ARCHITECTURE.md`
- One-time setup, deploy, rollback, secret rotation, restore and post-deploy checks:
  `docs/deploy/RUNBOOK.md`
- Workflows: `.github/workflows/ci.yml`, `_deploy.yml`, `deploy-staging.yml`, `deploy-prod.yml`
  (variables and environments: `docs/deploy/agents/ci-and-images.md` and `docs/deploy/REPORT.md`)

The manual scripts this section used to describe (`scripts/deploy_to_cloud_run.sh`,
`scripts/deploy_gcp.sh`, `docker/cloudbuild.yaml`) were removed in Phase 4C.

---

## 3. Database Administration & Migrations

### Applying Migrations
Alembic migrations are executed automatically during container startup or via:
```bash
# Run migrations using alembic
alembic upgrade head
```

### Seeding Development Credits & Initial Tenant
In development or demo mode, ensure the tenant wallet has credits:
```sql
INSERT INTO wallets (tenant_id, balance) 
VALUES ('00000000-0000-4000-8000-00000000d0c7', 1000.0000) 
ON CONFLICT (tenant_id) DO UPDATE SET balance = 1000.0000;
```

---

## 4. Verification Checkpoints

1. **Web App Check**:
   ```bash
   curl -s -I http://localhost:3000/
   # Expect: HTTP/1.1 200 OK
   ```

2. **Backend Health Check**:
   ```bash
   curl -s http://localhost:8080/v1/health
   # Expect: {"status":"healthy"}
   ```

3. **Authentication Token Verification**:
   ```bash
   TOKEN=$(curl -s http://localhost:9090/token -H "Content-Type: application/json" -d '{"sub":"doctor","tenant_id":"00000000-0000-4000-8000-00000000d0c7","role":"admin","mfa":true}' | jq -r .access_token)
   curl -s http://localhost:8080/v1/sso/session -H "Authorization: Bearer $TOKEN"
   # Expect: {"user_id":"doctor","role":"admin","credential":"jwt", ...}
   ```
