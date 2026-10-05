#!/usr/bin/env bash
# PRINTS the commands that deploy staging. It runs nothing: every command touches the owner's
# Google Cloud project, credentials or billing, so the owner runs them (docs/runbooks/deploy.md).
# Placeholders in CAPITALS must be replaced; values are never guessed here.
cat <<'CMDS'
# 0. One time per project (see docs/runbooks/bootstrap.md): project, billing, state bucket.
gcloud auth login
gcloud config set project STAGING_PROJECT_ID
gcloud storage buckets create gs://STAGING_TF_STATE_BUCKET --location=asia-south1 --uniform-bucket-level-access

# 1. Infrastructure (review the plan before applying).
cd infra/envs/staging
cp terraform.tfvars.example terraform.tfvars   # then edit the values
terraform init -backend-config="bucket=STAGING_TF_STATE_BUCKET" -backend-config="prefix=staging"
terraform plan -out=staging.plan
terraform apply staging.plan

# 2. Secrets (values typed by the owner; never in git).
printf '%s' 'PROVIDER-KEY' | gcloud secrets versions add p-search-api-key --data-file=-
printf '%s' 'postgresql://...@/ai?host=/cloudsql/CONNECTION_NAME' | gcloud secrets versions add checkpoint-database-url --data-file=-

# 3. Image and migrations.
gcloud auth configure-docker asia-south1-docker.pkg.dev
docker build -t asia-south1-docker.pkg.dev/STAGING_PROJECT_ID/ai-staging-images/account-intelligence:GIT_SHA .
docker push asia-south1-docker.pkg.dev/STAGING_PROJECT_ID/ai-staging-images/account-intelligence:GIT_SHA
#    run alembic upgrade head as the migrator (Cloud SQL Auth Proxy session; docs/runbooks/database.md)

# 4. Roll out the image to both services.
gcloud run deploy ai-staging-api --region asia-south1 --image asia-south1-docker.pkg.dev/STAGING_PROJECT_ID/ai-staging-images/account-intelligence:GIT_SHA
gcloud run deploy ai-staging-worker --region asia-south1 --image asia-south1-docker.pkg.dev/STAGING_PROJECT_ID/ai-staging-images/account-intelligence:GIT_SHA

# 5. Verify: readiness must be 200 (it fails closed).
curl -fsS "$(terraform output -raw api_url)/readyz"
CMDS
