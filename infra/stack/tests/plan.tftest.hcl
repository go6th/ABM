# Offline plan of the whole stack against mock providers: no credentials, no API calls, nothing
# created. Asserts the security and deployment properties the release depends on.
# Run: terraform -chdir=infra/stack init -backend=false && terraform -chdir=infra/stack test

mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
  mock_data "google_storage_project_service_account" {
    defaults = { email_address = "service-123@gs-project-accounts.iam.gserviceaccount.com" }
  }
}
mock_provider "google-beta" {}

variables {
  environment       = "staging"
  project_id        = "synthetic-staging"
  region            = "us-central1"
  billing_account   = "000000-000000-000000"
  domain            = "app.synthetic.test"
  github_repository = "owner/repo"
  deploy_ref        = "refs/heads/main"
  images = {
    api    = "r-docker.pkg.dev/p/i/api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    worker = "r-docker.pkg.dev/p/i/worker@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    web    = "r-docker.pkg.dev/p/i/web@sha256:2222222222222222222222222222222222222222222222222222222222222222"
  }
  db_tier            = "db-custom-2-7680"
  db_availability    = "ZONAL"
  llm_access         = "anthropic_api"
  monthly_budget_usd = 500
  alert_channels     = []
  raw_retention_days = 30
  staff = {
    workspace_domain = "synthetic.test"
    operator_emails  = "op@synthetic.test"
    support_emails   = ""
    oauth_client_id  = "client.apps.googleusercontent.com"
  }
  min_instances = { api = 0, web = 0 }
}

run "staging_plan" {
  command = plan

  assert {
    condition     = module.cloud_run.names["api"] == "ai-staging-api"
    error_message = "api service name"
  }
  assert {
    condition     = output.api_url == "https://ai-staging-api-123456789012.us-central1.run.app"
    error_message = "deterministic api URL"
  }
  assert {
    condition     = length(output.jobs) == 3 && contains(keys(output.jobs), "run")
    error_message = "run, migrate and spend-invariants jobs"
  }
}

run "images_must_be_pinned_by_digest" {
  command = plan
  variables {
    images = { api = "r/api:latest", worker = "r/worker:latest", web = "r/web:latest" }
  }
  expect_failures = [var.images]
}

run "unknown_environment_is_refused" {
  command = plan
  variables {
    environment = "dev"
  }
  expect_failures = [var.environment]
}

run "unknown_model_access_is_refused" {
  command = plan
  variables {
    llm_access = "openai"
  }
  expect_failures = [var.llm_access]
}

run "prod_plan" {
  command = plan
  variables {
    environment     = "prod"
    project_id      = "synthetic-prod"
    db_availability = "REGIONAL"
  }
  assert {
    condition     = output.services["web"] == "ai-prod-web"
    error_message = "prod naming"
  }
}
