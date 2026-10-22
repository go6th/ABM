#!/usr/bin/env bash
# Smoke tests against a deployed environment (staging or production). Read-only: GET requests and
# one unauthenticated POST that must be refused. No credential is used or printed.
#   1. health: the web server answers /healthz, and /readyz (which asks the api) is 200
#   2. the sign-in page renders
#   3. isolation probe: tenant routes refuse a missing and a forged credential, task routes refuse
#      a request without a Google-issued OIDC token
#   4. the deployed services run the digests this workflow pushed
#   5. packaging import check inside the deployed api and worker images
# The hermetic research run is NOT here: staging refuses LLM_FAKE and CONNECTORS=hermetic by
# design, so the run happens in CI against the same images before they are deployed
# (docker/ci/hermetic_stack.sh).
# Environment (names only): APP_URL, GCP_PROJECT_ID, GCP_REGION, CLOUD_RUN_API_SERVICE,
#   CLOUD_RUN_WORKER_SERVICE, CLOUD_RUN_WEB_SERVICE, API_DIGEST_REF, WORKER_DIGEST_REF, WEB_DIGEST_REF
set -Eeuo pipefail

: "${APP_URL:?}" "${GCP_PROJECT_ID:?}" "${GCP_REGION:?}" "${CLOUD_RUN_API_SERVICE:?}"
: "${CLOUD_RUN_WORKER_SERVICE:?}" "${CLOUD_RUN_WEB_SERVICE:?}"
: "${API_DIGEST_REF:?}" "${WORKER_DIGEST_REF:?}" "${WEB_DIGEST_REF:?}"
app="${APP_URL%/}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
ok() { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1" >&2; fail=1; }

# Cloud Run needs a moment to move traffic; retry the first request instead of sleeping blindly.
retry() { local n="$1"; shift; for _ in $(seq 1 "$n"); do "$@" && return 0; sleep 5; done; return 1; }

if retry 24 curl -fsS --max-time 10 -o /dev/null "$app/healthz"; then ok "web /healthz"; else bad "web /healthz"; fi
if retry 12 curl -fsS --max-time 10 -o /dev/null "$app/readyz"; then ok "readiness (web asks the api)"; else bad "readiness"; fi

page="$(curl -fsS --max-time 15 "$app/login" || true)"
if grep -qi "sign in" <<<"$page" && ! grep -qi "sign in is unavailable" <<<"$page"; then ok "sign-in page renders"; else bad "sign-in page"; fi

if python3 "$here/probe_isolation.py" --api "$app" --anonymous --tasks-statuses 401,403,404; then
  ok "isolation probe"
else
  bad "isolation probe"
fi

deployed_image() { # service -> image reference the latest ready revision runs
  gcloud run services describe "$1" --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
    --format='value(spec.template.spec.containers[0].image)'
}
if [[ "$(deployed_image "$CLOUD_RUN_API_SERVICE")" == "$API_DIGEST_REF" ]]; then
  ok "api service runs ${API_DIGEST_REF##*@}"
else
  bad "api service does not run the pushed digest"
fi
if [[ "$(deployed_image "$CLOUD_RUN_WORKER_SERVICE")" == "$WORKER_DIGEST_REF" ]]; then
  ok "worker service runs ${WORKER_DIGEST_REF##*@}"
else
  bad "worker service does not run the pushed digest"
fi
if [[ "$(deployed_image "$CLOUD_RUN_WEB_SERVICE")" == "$WEB_DIGEST_REF" ]]; then
  ok "web service runs ${WEB_DIGEST_REF##*@}"
else
  bad "web service does not run the pushed digest"
fi

for ref in "$API_DIGEST_REF" "$WORKER_DIGEST_REF"; do
  if docker run --rm --entrypoint python "$ref" -I -c \
    "import documents.sandbox_child, research_files.blobstore, devstore.server" >/dev/null 2>&1; then
    ok "packaging import check on ${ref##*@}"
  else
    bad "packaging import check on ${ref##*@}"
  fi
done
exit "$fail"
