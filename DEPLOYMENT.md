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

### Target Architecture
- **Web App**: Cloud Run v2 (Serverless container, auto-scaling, custom domain, Cloud Armor).
- **API**: Cloud Run v2 (Private VPC ingress from web, IAM service-to-service auth).
- **Worker**: Cloud Run Jobs (One-job-per-run execution driven by Cloud Tasks).
- **Database**: Cloud SQL for PostgreSQL 16 (Private IP, automated backups, CMEK).
- **Storage**: Google Cloud Storage (Private buckets for research artifacts & PDF exports).
- **Secrets**: Google Cloud Secret Manager (Managed secret injection).

### Prerequisites
1. GCP Project ID (with Billing enabled).
2. Enabled APIs: `run.googleapis.com`, `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`, `sqladmin.googleapis.com`.
3. Service accounts created with least-privilege roles (`roles/cloudsql.client`, `roles/secretmanager.secretAccessor`).

### Deployment Steps

#### Option A: Quick Automated Deployment Script
```bash
# 1. Authenticate with Google Cloud
gcloud auth login
gcloud config set project abmgenerator

# 2. Build, push images to Artifact Registry, and deploy to Cloud Run
bash scripts/deploy_to_cloud_run.sh --all
```

#### Option B: Step-by-Step Manual Deployment
```bash
# 1. Authenticate with Google Cloud
gcloud auth login
gcloud config set project abmgenerator

# 2. Configure Docker authentication
gcloud auth configure-docker asia-south1-docker.pkg.dev

# 3. Build container images locally
docker build -f docker/api.Dockerfile -t asia-south1-docker.pkg.dev/abmgenerator/abm-repo/api:latest .
docker build -f docker/worker.Dockerfile -t asia-south1-docker.pkg.dev/abmgenerator/abm-repo/worker:latest .
docker build -f web/Dockerfile -t asia-south1-docker.pkg.dev/abmgenerator/abm-repo/web:latest web

# (Alternatively, build in the cloud with Cloud Build)
gcloud builds submit --config=docker/cloudbuild.yaml .

# 4. Push images to Artifact Registry
docker push asia-south1-docker.pkg.dev/abmgenerator/abm-repo/api:latest
docker push asia-south1-docker.pkg.dev/abmgenerator/abm-repo/worker:latest
docker push asia-south1-docker.pkg.dev/abmgenerator/abm-repo/web:latest

# 5. Run Database Migrations (using Cloud Run Jobs)
gcloud run jobs create abm-migrate \
  --project=abmgenerator \
  --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/abmgenerator/abm-repo/api:latest \
  --command="alembic" \
  --args="upgrade,head" \
  --set-env-vars="SERVICE_ROLE=migrate,MIGRATOR_DATABASE_URL=$DATABASE_URL"

gcloud run jobs execute abm-migrate --project=abmgenerator --region=asia-south1 --wait

# 6. Deploy Backend API to Cloud Run
gcloud run deploy abm-api \
  --project=abmgenerator \
  --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/abmgenerator/abm-repo/api:latest \
  --port=8080 \
  --allow-unauthenticated \
  --set-env-vars="SERVICE_ROLE=api,DATABASE_URL=$DATABASE_URL,CHECKPOINT_DATABASE_URL=$CHECKPOINT_DATABASE_URL,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"

# 7. Deploy Worker to Cloud Run
gcloud run deploy abm-worker \
  --project=abmgenerator \
  --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/abmgenerator/abm-repo/worker:latest \
  --port=8081 \
  --command="uvicorn" \
  --args="account_intelligence.api.main:app,--host,0.0.0.0,--port,8081" \
  --allow-unauthenticated \
  --set-env-vars="SERVICE_ROLE=worker,DATABASE_URL=$DATABASE_URL,CHECKPOINT_DATABASE_URL=$CHECKPOINT_DATABASE_URL,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"

# 8. Deploy Frontend Web to Cloud Run
API_URL=$(gcloud run services describe abm-api --project=abmgenerator --region=asia-south1 --format='value(status.url)')

gcloud run deploy abm-web \
  --project=abmgenerator \
  --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/abmgenerator/abm-repo/web:latest \
  --port=3000 \
  --allow-unauthenticated \
  --set-env-vars="API_ORIGIN=$API_URL"
```

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
