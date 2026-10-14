# Platform monitoring on Google-provided metrics and structured log events (see main.tf). Every
# alert names its runbook; severities follow docs/oncall (page: someone acts now; ticket: next
# working day). Thresholds are first proposals for low traffic; tune them on staging.

variable "name" {
  type        = string
  description = "Name prefix, e.g. ai-staging."
}
variable "domain" {
  type        = string
  description = "Public hostname of the web app (uptime checks go through the load balancer)."
}
variable "services" {
  type        = object({ web = string, api = string, worker = string })
  description = "Cloud Run service names."
}
variable "queues" {
  type        = map(string)
  description = "Cloud Tasks queue names by purpose."
}
variable "sql_instance" {
  type        = string
  description = "Cloud SQL instance name (not the connection name)."
}
variable "sql_max_connections" {
  type = number
}
variable "migrate_job" {
  type        = string
  description = "Cloud Run Job name of the migrations."
}
variable "uptime_period" {
  type        = string
  description = "Uptime check period: 60s, 300s, 600s or 900s."
  default     = "300s"
}
variable "task_backlog_threshold" {
  type        = number
  description = "Tasks waiting in one queue for 15 minutes before an alert."
  default     = 100
}

locals {
  run_services = "resource.type=\"cloud_run_revision\" AND resource.label.service_name=one_of(\"${var.services.web}\", \"${var.services.api}\")"
  sql_db       = "resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${var.sql_instance}\""
  # structured log events (obs/logging.py writes {"event": ...}); Cloud Run parses stdout JSON
  log_events = {
    isolation_check_failed = { event = "isolation_check_failed", doc = "Nightly isolation check failed" }
    runs_stalled           = { event = "runs.stalled", doc = "Runs marked stalled by the worker sweeper" }
    runs_worker_lost       = { event = "runs.swept", doc = "Runs ended worker_lost by the sweeper" }
    sweep_failed           = { event = "runs.sweep_failed", doc = "The worker's orphan sweep failed" }
  }
  uptime = {
    web = { path = "/healthz", doc = "web answers through the load balancer" }
    api = { path = "/readyz", doc = "web reaches the api and the api is ready (web/app/readyz)" }
  }
}

# ---------------------------------------------------------------------------------------------
# Uptime checks (the api has no public address: its check goes through web's /readyz, which asks
# the api's /readyz over the private path).

resource "google_monitoring_uptime_check_config" "check" {
  for_each     = local.uptime
  project      = var.project_id
  display_name = "${var.name} ${each.key} uptime"
  timeout      = "10s"
  period       = var.uptime_period
  http_check {
    path           = each.value.path
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_value = 200
    }
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.domain
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  for_each     = local.uptime
  project      = var.project_id
  display_name = "${var.name} ${each.key} down"
  combiner     = "OR"
  conditions {
    display_name = "${each.key} uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.check[each.key].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  notification_channels = var.page_channels
  documentation {
    content   = "Uptime: ${each.value.doc}. Runbook: docs/runbooks/api-availability-burn.md."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "page", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# Errors and latency (web and api; the worker answers 503 "busy" by design and runs long requests)

resource "google_monitoring_alert_policy" "errors_5xx" {
  project      = var.project_id
  display_name = "${var.name} 5xx errors"
  combiner     = "OR"
  conditions {
    display_name = "5xx responses above 0.1 per second for 5 minutes"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND metric.label.response_code_class=\"5xx\" AND ${local.run_services}"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.1
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }
  notification_channels = var.page_channels
  documentation {
    content   = "Runbook: docs/runbooks/api-availability-burn.md."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "page", owner = "oncall" }
}

resource "google_monitoring_alert_policy" "latency_p95" {
  project      = var.project_id
  display_name = "${var.name} latency p95"
  combiner     = "OR"
  conditions {
    display_name = "p95 request latency above 3 s for 10 minutes"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_latencies\" AND ${local.run_services}"
      comparison      = "COMPARISON_GT"
      threshold_value = 3000
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }
  notification_channels = var.ticket_channels
  documentation {
    content   = "Runbook: docs/runbooks/api-latency.md. Chat streams count as long requests."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "ticket", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# Cloud Tasks backlog: tasks waiting means the worker is down, refusing or saturated.

resource "google_monitoring_alert_policy" "task_backlog" {
  project      = var.project_id
  display_name = "${var.name} task backlog"
  combiner     = "OR"
  conditions {
    display_name = "a queue holds more than ${var.task_backlog_threshold} tasks for 15 minutes"
    condition_threshold {
      filter          = "metric.type=\"cloudtasks.googleapis.com/queue/depth\" AND resource.type=\"cloud_tasks_queue\" AND resource.label.queue_id=one_of(${join(", ", [for q in values(var.queues) : "\"${q}\""])})"
      comparison      = "COMPARISON_GT"
      threshold_value = var.task_backlog_threshold
      duration        = "900s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["resource.label.queue_id"]
      }
    }
  }
  notification_channels = var.page_channels
  documentation {
    content   = "Runbook: docs/runbooks/stuck-runs.md (worker revision ready? 403s mean the tasks identity or audience is wrong)."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "page", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# Cloud SQL: CPU, storage, memory, connections

resource "google_monitoring_alert_policy" "sql" {
  for_each = {
    cpu = {
      metric = "cloudsql.googleapis.com/database/cpu/utilization", above = 0.8,
      doc    = "CPU above 80% for 15 minutes: check Query Insights, then the tier."
    }
    disk = {
      metric = "cloudsql.googleapis.com/database/disk/utilization", above = 0.8,
      doc    = "Disk above 80% (autoresize grows it; this is the cost and runaway-growth signal)."
    }
    memory = {
      metric = "cloudsql.googleapis.com/database/memory/utilization", above = 0.9,
      doc    = "Memory above 90% for 15 minutes."
    }
    connections = {
      metric = "cloudsql.googleapis.com/database/postgresql/num_backends", above = floor(var.sql_max_connections * 0.8),
      doc    = "Connections above 80% of max_connections: instances x pool size (docs/deploy/ARCHITECTURE.md)."
    }
  }
  project      = var.project_id
  display_name = "${var.name} database ${each.key}"
  combiner     = "OR"
  conditions {
    display_name = "${each.key} above ${each.value.above}"
    condition_threshold {
      filter          = "metric.type=\"${each.value.metric}\" AND ${local.sql_db}"
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.above
      duration        = "900s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = each.key == "connections" ? "ALIGN_MAX" : "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }
  notification_channels = each.key == "disk" ? var.ticket_channels : var.page_channels
  documentation {
    content   = "${each.value.doc} Runbook: docs/runbooks/database.md."
    mime_type = "text/markdown"
  }
  user_labels = { severity = each.key == "disk" ? "ticket" : "page", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# Failed migrations (the deploy stops on them too; this catches a manual execution)

resource "google_monitoring_alert_policy" "migration_failed" {
  project      = var.project_id
  display_name = "${var.name} migration failed"
  combiner     = "OR"
  conditions {
    display_name = "migrate job execution failed"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.job_name=\"${var.migrate_job}\" AND metric.label.result=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
  notification_channels = var.page_channels
  documentation {
    content   = "A schema migration failed: do not roll out the new revision. Runbook: docs/deploy/RUNBOOK.md (deploy, step 3)."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "page", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# Log events: the nightly isolation check, stalled and orphaned runs, sweeper failures, and any
# Cloud Scheduler tick that errors (a check that did not run is a failed check).

resource "google_logging_metric" "event" {
  for_each = local.log_events
  project  = var.project_id
  name     = "${replace(var.name, "-", "_")}_${each.key}"
  filter   = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.services.worker}\" AND jsonPayload.event=\"${each.value.event}\""
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = each.value.doc
  }
}

resource "google_logging_metric" "scheduler_failed" {
  project = var.project_id
  name    = "${replace(var.name, "-", "_")}_scheduler_failed"
  filter  = "resource.type=\"cloud_scheduler_job\" AND severity>=ERROR"
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "Cloud Scheduler attempts that failed"
    labels {
      key        = "job_id"
      value_type = "STRING"
    }
  }
  label_extractors = {
    job_id = "EXTRACT(resource.labels.job_id)"
  }
}

resource "google_monitoring_alert_policy" "event" {
  for_each = {
    isolation_check_failed = { severity = "page", runbook = "isolation-violation.md", window = "0s" }
    runs_stalled           = { severity = "page", runbook = "stuck-runs.md", window = "0s" }
    runs_worker_lost       = { severity = "ticket", runbook = "stuck-runs.md", window = "0s" }
    sweep_failed           = { severity = "page", runbook = "stuck-runs.md", window = "1800s" }
  }
  project      = var.project_id
  display_name = "${var.name} ${replace(each.key, "_", " ")}"
  combiner     = "OR"
  conditions {
    display_name = local.log_events[each.key].doc
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.event[each.key].name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = each.value.window
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
  notification_channels = each.value.severity == "page" ? var.page_channels : var.ticket_channels
  documentation {
    content   = "Runbook: docs/runbooks/${each.value.runbook}."
    mime_type = "text/markdown"
  }
  user_labels = { severity = each.value.severity, owner = "oncall" }
}

resource "google_monitoring_alert_policy" "scheduler_failed" {
  project      = var.project_id
  display_name = "${var.name} scheduled job failed"
  combiner     = "OR"
  conditions {
    display_name = "a Cloud Scheduler tick failed (isolation check, schedules, expiry, nightly)"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.scheduler_failed.name}\" AND resource.type=\"cloud_scheduler_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["metric.label.job_id"]
      }
    }
  }
  notification_channels = var.page_channels
  documentation {
    content   = "Runbook: docs/runbooks/stuck-scheduler.md. A failed isolation-check tick counts as a failed isolation check."
    mime_type = "text/markdown"
  }
  user_labels = { severity = "page", owner = "oncall" }
}

# ---------------------------------------------------------------------------------------------
# The platform dashboard

locals {
  tiles = [
    { title = "Requests per second by service", filter = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\"", aligner = "ALIGN_RATE", reducer = "REDUCE_SUM", group = ["resource.label.service_name"] },
    { title = "5xx per second by service", filter = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND metric.label.response_code_class=\"5xx\"", aligner = "ALIGN_RATE", reducer = "REDUCE_SUM", group = ["resource.label.service_name"] },
    { title = "p95 latency (ms) by service", filter = "metric.type=\"run.googleapis.com/request_latencies\" AND resource.type=\"cloud_run_revision\"", aligner = "ALIGN_PERCENTILE_95", reducer = "REDUCE_MAX", group = ["resource.label.service_name"] },
    { title = "Instances by service", filter = "metric.type=\"run.googleapis.com/container/instance_count\" AND resource.type=\"cloud_run_revision\"", aligner = "ALIGN_MAX", reducer = "REDUCE_SUM", group = ["resource.label.service_name"] },
    { title = "Memory utilisation p99 by service", filter = "metric.type=\"run.googleapis.com/container/memory/utilizations\" AND resource.type=\"cloud_run_revision\"", aligner = "ALIGN_PERCENTILE_99", reducer = "REDUCE_MAX", group = ["resource.label.service_name"] },
    { title = "Tasks waiting by queue", filter = "metric.type=\"cloudtasks.googleapis.com/queue/depth\" AND resource.type=\"cloud_tasks_queue\"", aligner = "ALIGN_MAX", reducer = "REDUCE_MAX", group = ["resource.label.queue_id"] },
    { title = "Database CPU", filter = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND ${local.sql_db}", aligner = "ALIGN_MEAN", reducer = "REDUCE_MAX", group = [] },
    { title = "Database connections", filter = "metric.type=\"cloudsql.googleapis.com/database/postgresql/num_backends\" AND ${local.sql_db}", aligner = "ALIGN_MAX", reducer = "REDUCE_SUM", group = [] },
    { title = "Database disk utilisation", filter = "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\" AND ${local.sql_db}", aligner = "ALIGN_MEAN", reducer = "REDUCE_MAX", group = [] },
    { title = "Job executions by result", filter = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\"", aligner = "ALIGN_SUM", reducer = "REDUCE_SUM", group = ["resource.label.job_name", "metric.label.result"] },
  ]
}

resource "google_monitoring_dashboard" "platform" {
  project = var.project_id
  dashboard_json = jsonencode({
    displayName = "${var.name} platform"
    mosaicLayout = {
      columns = 12
      tiles = [for i, t in local.tiles : {
        xPos   = (i % 2) * 6
        yPos   = floor(i / 2) * 4
        width  = 6
        height = 4
        widget = {
          title = t.title
          xyChart = {
            dataSets = [{
              plotType = "LINE"
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = t.filter
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = t.aligner
                    crossSeriesReducer = t.reducer
                    groupByFields      = t.group
                  }
                }
              }
            }]
          }
        }
      }]
    }
  })
}
