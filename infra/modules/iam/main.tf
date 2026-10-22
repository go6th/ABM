# Least-privilege identities: one service account per workload, project roles limited to what each
# one does, and resource-level grants (secrets, bucket, jobs, services) made in the module that owns
# the resource. CI deploys through Workload Identity Federation (GitHub OIDC; no key file), limited
# to one repository and, per environment, to its deploy ref and GitHub environment.
#
#   api        Cloud Run service api: Cloud SQL (IAM auth), Cloud Tasks enqueue (slice 0, uploads,
#              approval timeouts), model access, logs/metrics/traces.
#   worker     Cloud Run service worker (the /tasks/* routes): Cloud SQL, Cloud Tasks enqueue (the
#              next slice), model access, logs/metrics/traces.
#   job        Cloud Run Job spend-invariants (nightly): Cloud SQL, logs/metrics.
#   web        Cloud Run service web: logs and metrics only (no data access).
#   migrate    Cloud Run Job migrate: Cloud SQL client, logs (the migrator URL is its secret).
#   tasks      identity Cloud Tasks and Cloud Scheduler sign worker calls with (run.invoker on the
#              worker only; it holds no other role).
#   scheduler  identity Cloud Scheduler uses to start jobs (granted on each job).
# Secret, bucket, KMS and invoker grants are resource-level, in the module that owns the resource.
#   deployer   what CI impersonates: deploy revisions, push images, run the migrate job, act as
#              the runtime accounts (granted per account, never project-wide).

variable "project_id" {
  type = string
}
variable "name" {
  type = string
}
variable "github_repository" {
  type        = string
  description = "owner/repo allowed to deploy."
}
variable "deploy_ref" {
  type        = string
  description = "Git ref allowed to deploy this environment (e.g. refs/heads/main)."
}
variable "github_environment" {
  type        = string
  description = "GitHub environment whose jobs may impersonate the deployer (protected for prod)."
}
variable "vertex_ai" {
  type        = bool
  description = "Grant model access through Vertex AI (LLM_ACCESS=vertex_ai)."
}

locals {
  accounts = {
    api       = "Account Intelligence API"
    worker    = "Account Intelligence worker"
    job       = "Account Intelligence nightly job"
    web       = "Account Intelligence web"
    migrate   = "Account Intelligence migrations"
    tasks     = "Cloud Tasks and Scheduler OIDC identity (worker calls)"
    scheduler = "Cloud Scheduler job starter"
    deployer  = "CI deployer (Workload Identity Federation)"
  }
  telemetry = ["roles/logging.logWriter", "roles/monitoring.metricWriter", "roles/cloudtrace.agent"]
  sql       = ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]
  model     = var.vertex_ai ? ["roles/aiplatform.user"] : []
  project_roles = {
    api     = concat(local.sql, local.telemetry, local.model, ["roles/cloudtasks.enqueuer"])
    worker  = concat(local.sql, local.telemetry, local.model, ["roles/cloudtasks.enqueuer"])
    job     = concat(local.sql, ["roles/logging.logWriter", "roles/monitoring.metricWriter"])
    web     = ["roles/logging.logWriter", "roles/monitoring.metricWriter"]
    migrate = concat(local.sql, ["roles/logging.logWriter"])
    # run.developer is granted per service and per job (modules/cloud_run `developers`,
    # modules/cloud_run_jobs `runners`), never project-wide
    deployer = ["roles/artifactregistry.writer"]
  }
  bindings = merge([
    for sa, roles in local.project_roles : { for r in roles : "${sa}:${r}" => { sa = sa, role = r } }
  ]...)
}

resource "google_service_account" "sa" {
  for_each     = local.accounts
  project      = var.project_id
  account_id   = "${var.name}-${each.key}"
  display_name = each.value
}

resource "google_project_iam_member" "role" {
  for_each = local.bindings
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.sa[each.value.sa].email}"
}

# The api and the worker create Cloud Tasks whose OIDC token is minted for the tasks account.
resource "google_service_account_iam_member" "enqueuer_acts_as_tasks" {
  for_each           = toset(["api", "worker"])
  service_account_id = google_service_account.sa["tasks"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa[each.value].email}"
}

moved {
  from = google_service_account_iam_member.api_acts_as_tasks
  to   = google_service_account_iam_member.enqueuer_acts_as_tasks["api"]
}

# CI deploys services and jobs that run as these accounts (per account, not project-wide).
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each           = toset(["api", "worker", "job", "web", "migrate"])
  service_account_id = google_service_account.sa[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa["deployer"].email}"
}

resource "google_iam_workload_identity_pool" "ci" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name}-ci"
  display_name              = "CI"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  # checkov:skip=CKV_GCP_125:the condition pins sub, repository, ref and environment (tests.tftest.hcl); checkov cannot resolve var.github_repository in the sub claim
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub Actions"
  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.ref"         = "assertion.ref"
    "attribute.environment" = "assertion.environment"
  }
  # Only this repository, this ref and this (protected) GitHub environment can exchange a token.
  # (a job that names a GitHub environment gets sub "repo:OWNER/REPO:environment:NAME")
  attribute_condition = join(" && ", [
    "assertion.sub == \"repo:${var.github_repository}:environment:${var.github_environment}\"",
    "assertion.repository == \"${var.github_repository}\"",
    "assertion.ref == \"${var.deploy_ref}\"",
    "assertion.environment == \"${var.github_environment}\"",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_impersonates_deployer" {
  service_account_id = google_service_account.sa["deployer"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.ci.name}/attribute.environment/${var.github_environment}"
}

output "emails" {
  value       = { for k, sa in google_service_account.sa : k => sa.email }
  description = "Service account email by workload."
}
output "wif_provider" {
  value       = google_iam_workload_identity_pool_provider.github.name
  description = "Full provider resource name for google-github-actions/auth."
}
