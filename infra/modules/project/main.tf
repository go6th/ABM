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
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

output "services" {
  value       = [for s in google_project_service.enabled : s.service]
  description = "Enabled service names."
}
