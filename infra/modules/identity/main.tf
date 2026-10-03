# Service accounts and least-privilege roles, plus Workload Identity Federation so CI deploys
# without a key file (GitHub OIDC tokens, restricted to one repository and its main branch).
#
# runtime: the api and worker services (Cloud SQL client, Vertex AI user, trace and log writers,
#          Cloud Tasks enqueuer, act-as the tasks account for OIDC tokens).
# tasks:   the identity Cloud Tasks signs worker calls with (run.invoker on the worker only).
# deployer: what CI impersonates (deploy Cloud Run, push images, act as runtime).

variable "project_id" {
  type = string
}
variable "name" {
  type = string
}
variable "github_repository" {
  type        = string
  description = "owner/repo allowed to deploy (attribute condition on the WIF provider)."
}
variable "deploy_ref" {
  type        = string
  default     = "refs/heads/main"
  description = "Git ref allowed to deploy this environment."
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.name}-runtime"
  display_name = "Account Intelligence runtime (api and worker)"
}

resource "google_service_account" "tasks" {
  project      = var.project_id
  account_id   = "${var.name}-tasks"
  display_name = "Cloud Tasks OIDC identity for the worker"
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "${var.name}-deployer"
  display_name = "CI deployer (via Workload Identity Federation)"
}

locals {
  runtime_roles = [
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtasks.enqueuer",
  ]
  deployer_roles = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
  ]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset(local.runtime_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# The runtime creates tasks that carry an OIDC token for the tasks account.
resource "google_service_account_iam_member" "runtime_acts_as_tasks" {
  service_account_id = google_service_account.tasks.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime.email}"
}

# CI deploys services that run as the runtime account.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "ci" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name}-ci"
  display_name              = "CI"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.ci.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub Actions"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"${var.deploy_ref}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.ci.name}/attribute.repository/${var.github_repository}"
}

output "runtime_email" {
  value = google_service_account.runtime.email
}
output "tasks_email" {
  value = google_service_account.tasks.email
}
output "deployer_email" {
  value = google_service_account.deployer.email
}
output "wif_provider" {
  value       = google_iam_workload_identity_pool_provider.github.name
  description = "Full provider resource name for the CI auth step."
}
