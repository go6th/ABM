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
```bash
# Authenticate with Google Cloud
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>

# Build and push container images to Artifact Registry
gcloud builds submit --config=docker/cloudbuild.yaml .

# Deploy database migrations
gcloud run jobs execute migrate --region=us-central1

# Deploy API and Web services to Cloud Run
gcloud run deploy api \
  --image=us-central1-docker.pkg.dev/<YOUR_PROJECT_ID>/abm/api:latest \
  --region=us-central1 \
  --vpc-connector=abm-vpc-conn \
  --set-env-vars=ENV=production

gcloud run deploy web \
  --image=us-central1-docker.pkg.dev/<YOUR_PROJECT_ID>/abm/web:latest \
  --region=us-central1 \
  --set-env-vars=API_ORIGIN=https://api-internal-url
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
