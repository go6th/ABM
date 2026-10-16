# Deployment

Two ways to run the application: the local Docker Compose stack, and Google Cloud Run built by
Terraform. Both run the same three images (`docker/README.md`).

## 1. Local (Docker Compose)

`make up` builds and starts everything in `compose.yml`:

| Service | Port | Image | Purpose | Health |
| --- | --- | --- | --- | --- |
| `web` | 3000 | `account-intelligence-web:local` | the Next.js app | `GET /healthz` |
| `api` | 8080 | `account-intelligence-api:local` | FastAPI, `SERVICE_ROLE=api` | `GET /healthz`, `GET /readyz` |
| `worker` | 8081 | `account-intelligence-worker:local` | the same app serving `/tasks/*`, `SERVICE_ROLE=worker` | `GET /healthz` |
| `migrate` | | api image | one-shot `alembic upgrade head` before the api starts | exits 0 |
| `db` | 5432 | `pgvector/pgvector:pg16` | PostgreSQL 16 with pgvector and forced RLS | `pg_isready` |
| `redis` | 6379 | `redis:7-alpine` | rate limits, research-cache index | `redis-cli ping` |
| `tasks` | 8123 | cloud-tasks-emulator | Cloud Tasks stand-in | |
| `gcs` | 4443 | fake-gcs-server | Cloud Storage stand-in | |
| `fake-idp` | 9090 | worker image + `ops/dev/fake_idp.py` | synthetic OIDC issuer | |

Hermetic by default (no container reads `.env`). Real keys, deliberately:
`docker compose -f compose.yml -f compose.live.yml up -d`. Stop with `make down`; wipe the
volumes with `make reset`. Logs: `make logs`. Database shell: `make db-shell`.

## 2. Google Cloud (staging today, production prepared)

What exists: project `abmgenerator`, region `us-central1`, Cloud Run services `ai-staging-api`,
`ai-staging-worker` (with the clamd scan sidecar), `ai-staging-web`, the Cloud Run Jobs
`ai-staging-migrate` and `spend-invariants`, Cloud SQL, Memorystore, the data bucket, the
Cloud Tasks queues `runs`, `documents`, `jobs`, the Cloud Scheduler ticks, Secret Manager
containers, the HTTPS load balancer with Cloud Armor, and the Artifact Registry
`ai-staging-images`. All of it is declared in `infra/stack/main.tf` and instantiated by
`infra/envs/staging` (`infra/README.md`).

Two release paths exist. Pushing to `main` with a green CI run triggers `deploy-staging.yml`
(the reusable `_deploy.yml` pushes the images CI built, signs them, runs the migration job,
deploys by digest, smoke tests and rolls back on failure). The hand release used so far is:

```bash
TAG=$(git rev-parse --short HEAD)            # build from a committed state
REG=us-central1-docker.pkg.dev/abmgenerator/ai-staging-images
docker build -f docker/api.Dockerfile    -t $REG/api:$TAG .      && docker push $REG/api:$TAG
docker build -f docker/worker.Dockerfile -t $REG/worker:$TAG .   && docker push $REG/worker:$TAG
docker build -t $REG/web:$TAG web                                && docker push $REG/web:$TAG
# take the pushed digests (docker inspect --format '{{index .RepoDigests 0}}' $REG/api:$TAG) and
gcloud run jobs update  ai-staging-migrate --region us-central1 --image $REG/api@sha256:…
gcloud run jobs execute ai-staging-migrate --region us-central1 --wait
gcloud run services update ai-staging-api    --region us-central1 --image $REG/api@sha256:…
gcloud run services update ai-staging-worker --region us-central1 --image $REG/worker@sha256:…
gcloud run services update ai-staging-web    --region us-central1 --image $REG/web@sha256:…
```

Then write the three digests into `infra/envs/staging/terraform.tfvars` (`images = {...}`), so
the next Terraform plan keeps them. Infrastructure changes (sizes, queues, ticks, variables) go
through `terraform plan -out` and `apply` of that plan file in `infra/envs/staging`.

Secrets are added to their Secret Manager containers by the owner, never through Terraform,
images or the repository (`docs/deploy/RUNBOOK.md`).

## 3. Database migrations

`migrations/README.md`. Locally the `migrate` service runs them; in the cloud the
`ai-staging-migrate` job runs them from the new api image before the services are updated.
`/readyz` refuses to serve when the schema revision is not the one the code expects.

## 4. After a release

```bash
curl -s https://<web host>/readyz          # 200 with the expected revision
curl -s https://<web host>/healthz
```

Then sign in, open an account, and check the Cloud Logging event `run.finished` for the first
research that completes. Alerts and dashboards are generated from `config/telemetry/`
(`docs/observability/README.md`).
