# Project services the platform needs (one project per environment, decision D-20).
# No resource here creates billing or a project; the project and its billing link are made by the
# owner (docs/runbooks/bootstrap.md) and passed in as var.project_id.

variable "project_id" {
  type        = string
  description = "Existing Google Cloud project for this environment."
}

locals {
  services = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "compute.googleapis.com",
    "cloudtasks.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "redis.googleapis.com",
    "aiplatform.googleapis.com",
    "identitytoolkit.googleapis.com",
    "cloudscheduler.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# No workload runs as the default compute service account: every Cloud Run service and job names
# its own account (modules/iam), so the default one is disabled once the Compute API is on.
resource "google_project_default_service_accounts" "off" {
  project        = var.project_id
  action         = "DISABLE"
  restore_policy = "REVERT_AND_IGNORE_FAILURE"
  depends_on     = [google_project_service.enabled]
}

output "services" {
  value       = [for s in google_project_service.enabled : s.service]
  description = "Enabled service names."
}
