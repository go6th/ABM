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
  clamav_image       = "r-docker.pkg.dev/p/i/clamav@sha256:3333333333333333333333333333333333333333333333333333333333333333"
  db_tier            = "db-custom-2-7680"
  db_availability    = "ZONAL"
  db_max_connections = 200
  redis_tier         = "BASIC"
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
  scaling = {
    api    = { min = 0, max = 3 }
    web    = { min = 0, max = 3 }
    worker = { min = 0, max = 2 }
  }
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
    condition     = output.worker_url == "https://ai-staging-worker-123456789012.us-central1.run.app"
    error_message = "deterministic worker URL (WORKER_URL and the scheduler ticks use it)"
  }
  assert {
    condition     = toset(keys(output.jobs)) == toset(["migrate", "spend-invariants", "reingest-tables"])
    error_message = "research runs are worker requests, not jobs: migrate, the nightly pass and the hand-run reingest pass"
  }
  assert {
    condition     = toset(keys(output.services)) == toset(["api", "web", "worker"])
    error_message = "web, api and worker services"
  }
  assert {
    condition     = output.queues == { runs = "ai-staging-runs", documents = "ai-staging-documents", jobs = "ai-staging-jobs" }
    error_message = "three Cloud Tasks queues"
  }
  assert {
    condition     = output.buckets.audit_archive == "ai-staging-audit-archive"
    error_message = "the audit archive bucket exists (applies on deploy)"
  }
  assert {
    condition     = toset(keys(output.service_accounts)) == toset(["api", "worker", "job", "web", "migrate", "tasks", "scheduler", "deployer"])
    error_message = "one service account per workload"
  }
}

run "secret_readers" {
  command = plan
  assert {
    condition     = contains(keys(module.secret_manager.secret_ids), "csrf-signing-key")
    error_message = "the CSRF signing key secret exists (security audit M6)"
  }
}

run "maildrive_and_moonshot_are_opt_in" {
  command = plan
  variables {
    moonshot_enabled = true
    maildrive        = { google_client_id = "g.apps.googleusercontent.com", microsoft_client_id = "" }
  }
  assert {
    condition     = contains(keys(module.secret_manager.secret_ids), "maildrive-google-client-secret") && !contains(keys(module.secret_manager.secret_ids), "maildrive-microsoft-client-secret")
    error_message = "a mail and drive client secret exists only for a configured client id"
  }
}

run "clamav_must_be_pinned_by_digest" {
  command = plan
  variables {
    clamav_image = "clamav/clamav:stable"
  }
  expect_failures = [var.clamav_image]
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
