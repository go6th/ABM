# Runbook: bootstrap an environment (owner only)

Every step needs the owner's Google Cloud account; nothing here was run by the build.

1. Create the project for the environment and link billing (console or `gcloud projects create`,
   `gcloud billing projects link`).
2. Create the Terraform state bucket: `gcloud storage buckets create gs://BUCKET --location=asia-south1 --uniform-bucket-level-access`.
3. `cd infra/envs/ENV && cp terraform.tfvars.example terraform.tfvars`, fill in the values
   (project id, GitHub repository, database tier you chose).
4. `terraform init -backend-config="bucket=BUCKET" -backend-config="prefix=ENV"`, then
   `terraform plan -out=ENV.plan`, review, `terraform apply ENV.plan`.
5. Add secret versions (runbooks/secrets.md), run migrations (runbooks/database.md), deploy
   (runbooks/deploy.md).
6. Set the GitHub repository variables from the Terraform outputs: `STAGING_WIF_PROVIDER`
   (`wif_provider`), `STAGING_DEPLOYER_SA` (`deployer_email`), `STAGING_PROJECT_ID`,
   `STAGING_REGION`; add required reviewers to the `staging` environment.
7. Check the open VERIFY items for the region and services (docs/open-items.md).
