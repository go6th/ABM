# Cloud Tasks queue for run slices and approval timeouts (decision D-18). The queue's own retries
# cover delivery of a task to the worker (an infrastructure failure class); provider 429/5xx are
# retried only by the graph node RetryPolicy (W6), so delivery attempts are capped low here.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "max_dispatches_per_second" {
  type    = number
  default = 5
}
variable "max_concurrent_dispatches" {
  type    = number
  default = 10
}

resource "google_cloud_tasks_queue" "runs" {
  project  = var.project_id
  name     = "${var.name}-runs"
  location = var.region
  rate_limits {
    max_dispatches_per_second = var.max_dispatches_per_second
    max_concurrent_dispatches = var.max_concurrent_dispatches
  }
  retry_config {
    max_attempts  = 5
    min_backoff   = "10s"
    max_backoff   = "300s"
    max_doublings = 4
  }
}

output "queue" {
  value = google_cloud_tasks_queue.runs.name
}
