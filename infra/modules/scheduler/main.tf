# Cloud Scheduler: starts Cloud Run Jobs on a schedule through the Cloud Run Admin API, with an
# OAuth token for the scheduler identity (which holds run.developer on those jobs only).

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "sa_email" {
  type = string
}
variable "time_zone" {
  type = string
}
variable "jobs" {
  type        = map(object({ schedule = string, job_name = string }))
  description = "Scheduler entry to its cron schedule and the Cloud Run Job it starts."
}

resource "google_cloud_scheduler_job" "run_job" {
  for_each         = var.jobs
  project          = var.project_id
  region           = var.region
  name             = "${var.name}-${each.key}"
  schedule         = each.value.schedule
  time_zone        = var.time_zone
  attempt_deadline = "320s"
  retry_config {
    retry_count = 1
  }
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${each.value.job_name}:run"
    oauth_token {
      service_account_email = var.sa_email
    }
  }
}
