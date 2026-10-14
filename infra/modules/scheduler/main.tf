# Cloud Scheduler (FIX_PROMPT 5.1).
#   jobs        start a Cloud Run Job through the Cloud Run Admin API, with an OAuth token of the
#               scheduler identity (which holds run.developer on those jobs only).
#   http_ticks  POST a /tasks/* route on the private worker with an OIDC token of the tasks
#               identity for the worker's fixed audience: the same identity and audience the
#               application checks on every task (api/routes/tasks.py). A tick marked paused is
#               created paused ("applies on deploy"): its route ships in FIX_PROMPT Phase 5, and
#               the owner resumes it then.

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
  type        = string
  description = "Identity that starts Cloud Run Jobs (OAuth)."
}
variable "time_zone" {
  type = string
}
variable "jobs" {
  type        = map(object({ schedule = string, job_name = string }))
  description = "Scheduler entry to its cron schedule and the Cloud Run Job it starts."
}
variable "worker_url" {
  type        = string
  description = "Base URL of the private worker service."
}
variable "tick_sa_email" {
  type        = string
  description = "Identity whose OIDC token the worker accepts (the tasks identity)."
}
variable "tick_audience" {
  type = string
}
variable "http_ticks" {
  type = map(object({
    schedule = string
    path     = string
    deadline = string
    paused   = bool
  }))
  description = "Scheduler entry to its cron schedule and the worker route it calls."
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

resource "google_cloud_scheduler_job" "tick" {
  for_each         = var.http_ticks
  project          = var.project_id
  region           = var.region
  name             = "${var.name}-${each.key}"
  schedule         = each.value.schedule
  time_zone        = var.time_zone
  attempt_deadline = each.value.deadline
  paused           = each.value.paused
  retry_config {
    retry_count          = 1
    min_backoff_duration = "30s"
  }
  http_target {
    http_method = "POST"
    uri         = "${trimsuffix(var.worker_url, "/")}${each.value.path}"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode("{}")
    oidc_token {
      service_account_email = var.tick_sa_email
      audience              = var.tick_audience
    }
  }
}

output "ticks" {
  value = { for k, j in google_cloud_scheduler_job.tick : k => j.name }
}
