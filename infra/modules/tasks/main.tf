# Cloud Tasks queues (decision D-18, ADR-0018). Every task is an HTTP POST of ids only to the private
# worker service with an OIDC token of the tasks identity.
#   runs       run slices (one 240 s slice per request, then the next slice is enqueued) and
#              approval timeouts
#   documents  document and research-file processing (one part loop per request; parallel parts
#              are parallel tasks, at most max_parallel_parts per client)
#   jobs       confirmed research jobs, Download everything archives (short requests that start
#              runs or build one archive)
# The queue's own retries cover delivery to the worker (an infrastructure failure class); provider
# 429/5xx are retried only by the graph node RetryPolicy (W6), so run attempts stay capped low.
# max_concurrent_dispatches bounds what the worker sees at once (docs/deploy/ARCHITECTURE.md).
# The dispatch deadline is set per task by the application (Cloud Tasks default 10 min, max 30 min;
# VERIFY(V-TASKS-1)).

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "queues" {
  type = map(object({
    max_dispatches_per_second = number
    max_concurrent_dispatches = number
    max_attempts              = number
    min_backoff               = string
    max_backoff               = string
    max_doublings             = number
  }))
  validation {
    condition     = alltrue([for k in ["runs", "documents", "jobs"] : contains(keys(var.queues), k)])
    error_message = "queues must define runs, documents and jobs."
  }
}

resource "google_cloud_tasks_queue" "q" {
  for_each = var.queues
  project  = var.project_id
  name     = "${var.name}-${each.key}"
  location = var.region
  rate_limits {
    max_dispatches_per_second = each.value.max_dispatches_per_second
    max_concurrent_dispatches = each.value.max_concurrent_dispatches
  }
  retry_config {
    max_attempts  = each.value.max_attempts
    min_backoff   = each.value.min_backoff
    max_backoff   = each.value.max_backoff
    max_doublings = each.value.max_doublings
  }
  stackdriver_logging_config {
    sampling_ratio = 1.0 # every dispatch attempt is logged (low volume; failed runs are debuggable)
  }
}

output "queues" {
  value       = { for k, q in google_cloud_tasks_queue.q : k => q.name }
  description = "Queue short names by purpose."
}
output "queue" {
  value       = google_cloud_tasks_queue.q["runs"].name
  description = "The runs queue (TASKS_QUEUE)."
}

moved {
  from = google_cloud_tasks_queue.runs
  to   = google_cloud_tasks_queue.q["runs"]
}
