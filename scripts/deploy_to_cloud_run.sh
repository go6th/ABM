#!/usr/bin/env bash
# ==============================================================================
# deploy_to_cloud_run.sh - Deploy ABM Platform to Google Cloud Run
# ==============================================================================
set -Eeuo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "abmgenerator")}"
REGION="${GCP_REGION:-asia-south1}"
REPO="${ARTIFACT_REGISTRY:-abm-repo}"
TAG="${IMAGE_TAG:-latest}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"

API_IMAGE="${REGISTRY}/api:${TAG}"
WORKER_IMAGE="${REGISTRY}/worker:${TAG}"
WEB_IMAGE="${REGISTRY}/web:${TAG}"

log() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }

usage() {
  cat <<USAGE
Usage: $0 [options]

Options:
  --build         Build Docker images locally (api, worker, web)
  --push          Push Docker images to GCP Artifact Registry
  --migrate       Run Alembic database migrations via Cloud Run Job
  --deploy        Deploy Cloud Run services (api, worker, web)
  --all           Perform build, push, and deploy
  --help          Display this help message

Environment variables:
  GCP_PROJECT_ID  GCP project id (default: current gcloud project)
  GCP_REGION      GCP region (default: asia-south1)
  DATABASE_URL    PostgreSQL connection string for the API/worker
USAGE
  exit 0
}

DO_BUILD=0
DO_PUSH=0
DO_MIGRATE=0
DO_DEPLOY=0

if [[ $# -eq 0 ]]; then
  usage
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) DO_BUILD=1; shift ;;
    --push) DO_PUSH=1; shift ;;
    --migrate) DO_MIGRATE=1; shift ;;
    --deploy) DO_DEPLOY=1; shift ;;
    --all) DO_BUILD=1; DO_PUSH=1; DO_DEPLOY=1; shift ;;
    --help|-h) usage ;;
    *) err "Unknown option: $1"; usage ;;
  esac
done

log "Target GCP Project: $PROJECT_ID"
log "Target GCP Region:  $REGION"
log "Artifact Registry:  $REGISTRY"

# 1. Build Docker images
if [[ $DO_BUILD -eq 1 ]]; then
  log "Building Docker images..."
  docker build -f docker/api.Dockerfile -t "${API_IMAGE}" -t account-intelligence-api:local .
  docker build -f docker/worker.Dockerfile -t "${WORKER_IMAGE}" -t account-intelligence-worker:local .
  docker build -f web/Dockerfile -t "${WEB_IMAGE}" -t account-intelligence-web:local web
  log "Docker images built successfully."
fi

# 2. Push Docker images
if [[ $DO_PUSH -eq 1 ]]; then
  log "Configuring Docker auth for ${REGION}-docker.pkg.dev..."
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

  log "Pushing images to Artifact Registry..."
  docker push "${API_IMAGE}"
  docker push "${WORKER_IMAGE}"
  docker push "${WEB_IMAGE}"
  log "Docker images pushed successfully."
fi

# 3. Database Migration
if [[ $DO_MIGRATE -eq 1 ]]; then
  if [[ -z "${DATABASE_URL:-}" && -z "${MIGRATOR_DATABASE_URL:-}" ]]; then
    err "DATABASE_URL or MIGRATOR_DATABASE_URL must be provided to run database migrations."
    exit 1
  fi
  MIGRATE_URL="${MIGRATOR_DATABASE_URL:-$DATABASE_URL}"
  log "Executing database migrations via Cloud Run Job..."
  
  if ! gcloud run jobs describe abm-migrate --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
    gcloud run jobs create abm-migrate \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --image="${API_IMAGE}" \
      --command="alembic" \
      --args="upgrade,head" \
      --set-env-vars="SERVICE_ROLE=migrate,MIGRATOR_DATABASE_URL=${MIGRATE_URL}" \
      --quiet
  else
    gcloud run jobs update abm-migrate \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --image="${API_IMAGE}" \
      --set-env-vars="SERVICE_ROLE=migrate,MIGRATOR_DATABASE_URL=${MIGRATE_URL}" \
      --quiet
  fi

  gcloud run jobs execute abm-migrate --project="$PROJECT_ID" --region="$REGION" --wait
  log "Database migrations completed successfully."
fi

# 4. Deploy Services
if [[ $DO_DEPLOY -eq 1 ]]; then
  log "Deploying Backend API (abm-api) to Cloud Run..."
  API_ENV="SERVICE_ROLE=api"
  if [[ -n "${DATABASE_URL:-}" ]]; then
    API_ENV="${API_ENV},DATABASE_URL=${DATABASE_URL}"
  fi
  if [[ -n "${CHECKPOINT_DATABASE_URL:-}" ]]; then
    API_ENV="${API_ENV},CHECKPOINT_DATABASE_URL=${CHECKPOINT_DATABASE_URL}"
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    API_ENV="${API_ENV},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
  fi

  gcloud run deploy abm-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="${API_IMAGE}" \
    --port=8080 \
    --allow-unauthenticated \
    --set-env-vars="${API_ENV}" \
    --quiet

  API_URL="$(gcloud run services describe abm-api --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
  log "Backend API deployed at: ${API_URL}"

  log "Deploying Worker (abm-worker) to Cloud Run..."
  WORKER_ENV="SERVICE_ROLE=worker,ALLOW_UNAUTHENTICATED_TASKS=1"
  if [[ -n "${DATABASE_URL:-}" ]]; then
    WORKER_ENV="${WORKER_ENV},DATABASE_URL=${DATABASE_URL}"
  fi
  if [[ -n "${CHECKPOINT_DATABASE_URL:-}" ]]; then
    WORKER_ENV="${WORKER_ENV},CHECKPOINT_DATABASE_URL=${CHECKPOINT_DATABASE_URL}"
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    WORKER_ENV="${WORKER_ENV},ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
  fi

  gcloud run deploy abm-worker \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="${WORKER_IMAGE}" \
    --port=8081 \
    --command="uvicorn" \
    --args="account_intelligence.api.main:app,--host,0.0.0.0,--port,8081" \
    --allow-unauthenticated \
    --set-env-vars="${WORKER_ENV}" \
    --quiet

  WORKER_URL="$(gcloud run services describe abm-worker --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
  log "Worker service deployed at: ${WORKER_URL}"

  # Update API with WORKER_URL
  log "Linking API with WORKER_URL..."
  gcloud run services update abm-api \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --update-env-vars="WORKER_URL=${WORKER_URL}" \
    --quiet

  log "Deploying Frontend Web (abm-web) to Cloud Run..."
  gcloud run deploy abm-web \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --image="${WEB_IMAGE}" \
    --port=3000 \
    --allow-unauthenticated \
    --set-env-vars="API_ORIGIN=${API_URL}" \
    --quiet

  WEB_URL="$(gcloud run services describe abm-web --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
  log "Frontend Web UI deployed at: ${WEB_URL}"

  echo ""
  echo "=========================================================="
  echo "Deployment Complete!"
  echo "Backend API:  ${API_URL}"
  echo "Worker:       ${WORKER_URL}"
  echo "Frontend Web: ${WEB_URL}"
  echo "=========================================================="
fi
