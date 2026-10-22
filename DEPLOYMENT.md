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

### 2.1 Release path: the gated workflow

A release is a push to `main` with a green `ci.yml` run. `deploy-staging.yml` (the reusable
`_deploy.yml`) then pushes the images CI built and tested, signs them, runs the migration job
from the new api image, deploys every service by digest, runs `docker/ci/smoke_deployed.sh`
(health, sign-in page, isolation probe, the api, worker and web services run the pushed
digests, packaging import check) and rolls traffic back on failure. Nothing is rebuilt after
the tests passed, so the bytes in staging are the bytes CI inspected, scanned and exercised.
Production promotes the same digests by hand through `deploy-prod.yml`.

After the workflow finishes, write the deployed digests into
`infra/envs/staging/terraform.tfvars` (`images = {...}`) so a fresh environment is created from
the released images. This is bookkeeping, not protection: the Cloud Run module ignores image
changes (`ignore_changes = [image]` in `infra/modules/cloud_run` and `cloud_run_jobs`), so a plan
made from stale digests does not roll a service back; the deploy workflow and `gcloud` own the
running image.

### 2.2 Emergency path only: Cloud Build and gcloud by hand

When the workflow cannot run (GitHub or Workload Identity Federation down, a hotfix that must
ship before CI can finish), the owner may build and roll out by hand. This path skips CI, so it
is gated locally with `make release-check`, which runs the same checks the `images` job and the
deploy smoke test apply (`docker/ci/image_checks.sh`, Trivy on fixable HIGH and CRITICAL,
`docker/ci/smoke_deployed.sh`). Do not skip it; do not use this path for routine releases.

```bash
TAG=$(git rev-parse --short HEAD)            # build from a committed state
REG=us-central1-docker.pkg.dev/abmgenerator/ai-staging-images
# build on Cloud Build (a laptop build of the worker stalls on the NVIDIA wheels). .gcloudignore
# drops docker/, so pass an ignore file that keeps it; the worker is the CPU image unless
# UV_GROUP=gpu is added to its build args
printf '.git\n' > /tmp/release.gcloudignore
for name in api worker; do
  cat > /tmp/cloudbuild-$name.yaml <<EOF2
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, docker/$name.Dockerfile, -t, $REG/$name:$TAG, .]
images: [$REG/$name:$TAG]
options: { machineType: E2_HIGHCPU_8 }
EOF2
  gcloud builds submit --config /tmp/cloudbuild-$name.yaml --ignore-file /tmp/release.gcloudignore .
done
gcloud builds submit --tag $REG/web:$TAG web
# take the pushed digests (gcloud artifacts docker images describe $REG/api:$TAG --format 'value(image_summary.digest)')
docker pull $REG/api@sha256:… $REG/worker@sha256:… $REG/web@sha256:…
RELEASE_API_IMAGE=$REG/api@sha256:… RELEASE_WORKER_IMAGE=$REG/worker@sha256:… RELEASE_WEB_IMAGE=$REG/web@sha256:… \
  make release-check                          # inspection and Trivy before anything rolls out
gcloud run jobs update  ai-staging-migrate --region us-central1 --image $REG/api@sha256:…
gcloud run jobs execute ai-staging-migrate --region us-central1 --wait
gcloud run services update ai-staging-worker --region us-central1 --image $REG/worker@sha256:…
gcloud run services update ai-staging-api    --region us-central1 --image $REG/api@sha256:…
gcloud run services update ai-staging-web    --region us-central1 --image $REG/web@sha256:…
APP_URL=https://<web host> GCP_PROJECT_ID=abmgenerator GCP_REGION=us-central1 \
  CLOUD_RUN_API_SERVICE=ai-staging-api CLOUD_RUN_WORKER_SERVICE=ai-staging-worker CLOUD_RUN_WEB_SERVICE=ai-staging-web \
  API_DIGEST_REF=$REG/api@sha256:… WORKER_DIGEST_REF=$REG/worker@sha256:… WEB_DIGEST_REF=$REG/web@sha256:… \
  make release-check                          # the deployed smoke test, as the workflow runs it
```

If the smoke test fails, roll traffic back to the previous revision
(`docs/deploy/RUNBOOK.md`). Then record the digests in `terraform.tfvars` as in 2.1 and open the
pull request that carries the change through CI, so the next routine release is gated again.
Infrastructure changes (sizes, queues, ticks, variables) always go through `terraform plan -out`
and `apply` of that plan file in `infra/envs/staging`, never by hand.

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
