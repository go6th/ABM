#!/usr/bin/env bash
# Regenerate infra/envs/{dev,staging,production}/main.tf from main.tf.tmpl (values differ only in
# terraform.tfvars, which the owner keeps out of git). dev mirrors staging for a personal project.
set -euo pipefail
cd "$(dirname "$0")/envs"
for env in dev staging production; do
  avail=ZONAL; appenv=staging
  [ "$env" = production ] && { avail=REGIONAL; appenv=production; }
  sed -e "s/__ENV__/$env/g" -e "s/__AVAIL__/$avail/g" -e "s/__APPENV__/$appenv/g" main.tf.tmpl > "$env/main.tf"
  cat > "$env/terraform.tfvars.example" <<TFV
# Copy to terraform.tfvars (not committed) and fill in. Values below are placeholders.
project_id        = "REPLACE-WITH-THE-$env-PROJECT-ID"
github_repository = "OWNER/REPO"
image             = "REGION-docker.pkg.dev/PROJECT/ai-$env-images/account-intelligence@sha256:DIGEST"
db_tier           = "REPLACE-WITH-A-TIER-YOU-CHOSE"
TFV
done
cd "$(dirname "$0")/envs" && docker run --rm -v "$PWD/..":/infra -w /infra hashicorp/terraform:1.9 fmt -recursive >/dev/null
