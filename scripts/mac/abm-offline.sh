#!/usr/bin/env bash
# Switch ABM GCP staging OFF to stop the costs (project abmgenerator, us-central1).
# Nothing is deleted. Order: alerts first (so the stop does not page), then the
# database, task queues and scheduler jobs. Cloud Run already scales to zero.
# Run on a Mac with gcloud installed. Asks you to sign in if needed.
set -uo pipefail

PROJECT=abmgenerator
REGION=us-central1
SQL_INSTANCE=ai-staging-pg
QUEUES="ai-staging-runs ai-staging-jobs ai-staging-documents"
JOBS="ai-staging-schedules-tick ai-staging-isolation-check ai-staging-access-expiry ai-staging-audit-verify ai-staging-metrics ai-staging-maildrive-sync ai-staging-notify-relay ai-staging-spend-invariants ai-staging-sweep"

ensure_login() {
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "gcloud is not installed. Install it with: brew install --cask google-cloud-sdk"
    exit 1
  fi
  if [ -z "$(gcloud config get-value account 2>/dev/null)" ] || ! gcloud auth print-access-token >/dev/null 2>&1; then
    echo "Signing in to Google (a browser window opens)..."
    gcloud auth login --brief || exit 1
  fi
  gcloud config set project "$PROJECT" --quiet >/dev/null
  echo "Signed in as $(gcloud config get-value account 2>/dev/null), project $PROJECT"
}

ensure_login

read -r -p "Switch staging OFF (stops the database)? Type yes to continue: " answer
[ "$answer" = "yes" ] || { echo "Cancelled."; exit 0; }

echo "1/5 Disabling alert policies"
gcloud alpha monitoring policies list --project "$PROJECT" --format='value(name,enabled,displayName)' 2>/dev/null |
while IFS=$'\t' read -r policy enabled display; do
  if [ "$enabled" = "True" ]; then
    gcloud alpha monitoring policies update "$policy" --no-enabled --project "$PROJECT" --quiet >/dev/null 2>&1 \
      && echo "  disabled $display" || echo "  could not disable $display"
  fi
done

echo "2/5 Stopping Cloud Run services"
# Web stops taking public traffic (ingress internal). API and web drop their minimum of 1 instance.
# The worker already has a minimum of 0. Revisions are kept, so abm-online.sh can restore them.
gcloud run services update ai-staging-web --region "$REGION" --project "$PROJECT" --ingress=internal --min-instances=0 --quiet >/dev/null 2>&1 \
  && echo "  web: ingress internal, min 0" || echo "  could not update ai-staging-web"
gcloud run services update ai-staging-api --region "$REGION" --project "$PROJECT" --min-instances=0 --quiet >/dev/null 2>&1 \
  && echo "  api: min 0" || echo "  could not update ai-staging-api"

echo "3/5 Pausing scheduler jobs"
for j in $JOBS; do
  gcloud scheduler jobs pause "$j" --location "$REGION" --project "$PROJECT" --quiet >/dev/null 2>&1 \
    && echo "  paused $j" || echo "  could not pause $j (already paused?)"
done

echo "4/5 Pausing task queues"
for q in $QUEUES; do
  gcloud tasks queues pause "$q" --location "$REGION" --project "$PROJECT" --quiet >/dev/null 2>&1 \
    && echo "  paused $q" || echo "  could not pause $q (already paused?)"
done

echo "5/5 Stopping database $SQL_INSTANCE"
gcloud sql instances patch "$SQL_INSTANCE" --activation-policy=NEVER --project "$PROJECT" --quiet \
  && echo "  database stop requested" || echo "  database stop failed"

echo "Done. Still billing while off: Redis, load balancer, Cloud NAT, Artifact Registry, database storage."
