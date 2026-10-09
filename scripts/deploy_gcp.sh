#!/usr/bin/env bash
# WIF-authenticated GitHub deployment entry point. Runs only from deploy workflows.
set -Eeuo pipefail

required=(
  GCP_PROJECT_ID GCP_REGION ARTIFACT_REGISTRY CLOUD_RUN_API_SERVICE
  CLOUD_RUN_WEB_SERVICE CLOUD_RUN_RUN_JOB CLOUD_RUN_MIGRATE_JOB SOURCE_SHA
  ACTIONS_ID_TOKEN_REQUEST_URL ACTIONS_ID_TOKEN_REQUEST_TOKEN
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf 'deploy: required setting %s is missing\n' "$name" >&2
    exit 2
  fi
done
if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'deploy: source revision must be a full lowercase commit SHA\n' >&2
  exit 2
fi
if [[ ! "$ARTIFACT_REGISTRY" =~ ^[a-z0-9-]+-docker\.pkg\.dev/[a-z][a-z0-9-]+/[a-z][a-z0-9-]+$ ]]; then
  printf 'deploy: ARTIFACT_REGISTRY must be a regional Artifact Registry repository path\n' >&2
  exit 2
fi

sbom_dir="${RUNNER_TEMP:-/tmp}/deploy-sbom"
mkdir -p "$sbom_dir"
images=(api worker web)
declare -A tags digests
tags[api]="${ARTIFACT_REGISTRY}/api:${SOURCE_SHA}"
tags[worker]="${ARTIFACT_REGISTRY}/worker:${SOURCE_SHA}"
tags[web]="${ARTIFACT_REGISTRY}/web:${SOURCE_SHA}"

docker build --pull --file docker/api.Dockerfile --tag "${tags[api]}" .
docker build --pull --file docker/worker.Dockerfile --tag "${tags[worker]}" .
docker build --pull --file web/Dockerfile --tag "${tags[web]}" web

for image in "${images[@]}"; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.75.0 \
    image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "${tags[$image]}"
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$sbom_dir:/out" \
    aquasec/trivy:0.75.0 image --format cyclonedx --output "/out/${image}.cdx.json" "${tags[$image]}"
done

for image in "${images[@]}"; do
  docker push "${tags[$image]}"
  digest="$(docker inspect --format '{{index .RepoDigests 0}}' "${tags[$image]}")"
  if [[ ! "$digest" =~ @sha256:[0-9a-f]{64}$ ]]; then
    printf 'deploy: registry did not return a digest for %s\n' "$image" >&2
    exit 1
  fi
  digests[$image]="$digest"
done

# Cosign's container cannot use gcloud's credential helper, so provide a short-lived access token
# through a temporary mode-0700 Docker config. Both that file and the OIDC signing token are erased.
docker_config="$(mktemp -d)"
chmod 700 "$docker_config"
export DOCKER_CONFIG_DIR="$docker_config"
trap 'rm -rf "$docker_config"' EXIT
registry_host="${ARTIFACT_REGISTRY%%/*}"
GAR_ACCESS_TOKEN="$(gcloud auth print-access-token --project "$GCP_PROJECT_ID")"
export GAR_ACCESS_TOKEN registry_host
python3 - <<'PY'
import base64
import json
import os
from pathlib import Path

auth = base64.b64encode(f"oauth2accesstoken:{os.environ['GAR_ACCESS_TOKEN']}".encode()).decode()
Path(os.environ["DOCKER_CONFIG_DIR"], "config.json").write_text(
    json.dumps({"auths": {os.environ["registry_host"]: {"auth": auth}}})
)
PY
unset GAR_ACCESS_TOKEN registry_host
OIDC_TOKEN="$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sigstore" | jq -er '.value')"
for image in "${images[@]}"; do
  docker run --rm -e SIGSTORE_ID_TOKEN="$OIDC_TOKEN" -e DOCKER_CONFIG=/root/.docker \
    -v "$docker_config:/root/.docker:ro" ghcr.io/sigstore/cosign/cosign:v3.1.3 \
    sign --yes "${digests[$image]}"
done
unset OIDC_TOKEN

# Apply schema changes before starting code that depends on them. Runtime secrets and environment
# are already wired to Secret Manager by Terraform and are never supplied on these command lines.
gcloud run jobs update "$CLOUD_RUN_MIGRATE_JOB" --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" --image "${digests[api]}" --quiet
gcloud run jobs execute "$CLOUD_RUN_MIGRATE_JOB" --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" --wait
gcloud run jobs update "$CLOUD_RUN_RUN_JOB" --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" --image "${digests[worker]}" --quiet
gcloud run services update "$CLOUD_RUN_API_SERVICE" --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" --image "${digests[api]}" --quiet
gcloud run services update "$CLOUD_RUN_WEB_SERVICE" --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" --image "${digests[web]}" --quiet

for service in "$CLOUD_RUN_API_SERVICE" "$CLOUD_RUN_WEB_SERVICE"; do
  created="$(gcloud run services describe "$service" --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" --format='value(status.latestCreatedRevisionName)')"
  ready="$(gcloud run services describe "$service" --project "$GCP_PROJECT_ID" \
    --region "$GCP_REGION" --format='value(status.latestReadyRevisionName)')"
  if [[ -z "$created" || "$created" != "$ready" ]]; then
    printf 'deploy: service %s is not ready on its latest revision\n' "$service" >&2
    exit 1
  fi
done
printf 'deploy: rollout ready for revision %s\n' "$SOURCE_SHA"
