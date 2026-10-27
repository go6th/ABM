#!/usr/bin/env bash
# Switch ABM GCP staging ON (project abmgenerator, us-central1).
# Reverses abm-offline.sh: database first, then queues, scheduler jobs, alerts.
# Run on a Mac with gcloud installed. Asks you to sign in if needed.
set -uo pipefail

PROJECT=abmgenerator
REGION=us-central1
SQL_INSTANCE=ai-staging-pg
QUEUES="ai-staging-runs ai-staging-jobs ai-staging-documents"
JOBS="ai-staging-schedules-tick ai-staging-isolation-check ai-staging-access-expiry ai-staging-audit-verify ai-staging-metrics ai-staging-maildrive-sync ai-staging-notify-relay ai-staging-spend-invariants ai-staging-sweep"
# These two were left disabled on purpose (as found on 2026-10-20). Do not turn them on here.
KEEP_OFF="5xx errors|latency p95"

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

echo "1/5 Starting database $SQL_INSTANCE"
gcloud sql instances patch "$SQL_INSTANCE" --activation-policy=ALWAYS --project "$PROJECT" --quiet || echo "  database patch failed"
for i in $(seq 1 40); do
  state=$(gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT" --format='value(state)' 2>/dev/null)
  [ "$state" = "RUNNABLE" ] && break
  echo "  state: ${state:-unknown}, waiting 15 s"
  sleep 15
done
echo "  database state: ${state:-unknown}"

echo "2/5 Starting Cloud Run services"
# Restores what abm-offline.sh changed: web takes public traffic again, API and web keep 1 warm instance.
gcloud run services update ai-staging-api --region "$REGION" --project "$PROJECT" --min-instances=1 --quiet >/dev/null 2>&1 \
  && echo "  api: min 1" || echo "  could not update ai-staging-api"
gcloud run services update ai-staging-web --region "$REGION" --project "$PROJECT" --ingress=internal-and-cloud-load-balancing --min-instances=1 --quiet >/dev/null 2>&1 \
  && echo "  web: ingress load balancer, min 1" || echo "  could not update ai-staging-web"

echo "3/5 Resuming task queues"
for q in $QUEUES; do
  gcloud tasks queues resume "$q" --location "$REGION" --project "$PROJECT" --quiet >/dev/null 2>&1 \
    && echo "  resumed $q" || echo "  could not resume $q (already running?)"
done

echo "4/5 Resuming scheduler jobs"
for j in $JOBS; do
  gcloud scheduler jobs resume "$j" --location "$REGION" --project "$PROJECT" --quiet >/dev/null 2>&1 \
    && echo "  resumed $j" || echo "  could not resume $j (already running?)"
done

echo "5/5 Enabling alert policies"
gcloud alpha monitoring policies list --project "$PROJECT" --format='value(name,enabled,displayName)' 2>/dev/null |
while IFS=$'\t' read -r policy enabled display; do
  if [ "$enabled" = "False" ] && ! echo "$display" | grep -Eq "$KEEP_OFF"; then
    gcloud alpha monitoring policies update "$policy" --enabled --project "$PROJECT" --quiet >/dev/null 2>&1 \
      && echo "  enabled $display" || echo "  could not enable $display"
  fi
done

echo "Done. Check: https://136-81-60-29.sslip.io/readyz should return 200."
