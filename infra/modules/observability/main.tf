# Observability module (D-56): log sinks, log-based metrics, dashboards, alert policies and uptime
# checks for one environment. Two sources:
#   platform.tf                 hand-written: uptime checks, alerts on Google-provided metrics
#                               (Cloud Run, Cloud Tasks, Cloud SQL, Cloud Run Jobs, Scheduler) and
#                               log-based metrics, and the platform dashboard. These work from the
#                               first deploy.
#   generated_*.tf.json         from config/telemetry by scripts/gen_observability.py: alerts and
#                               dashboards on the application's workload.googleapis.com metrics.
#                               The alerts are created only with workload_alerts_enabled = true,
#                               once the OTLP exporter ships those metrics (VERIFY V-OBS-1):
#                               Monitoring refuses an alert on a metric that does not exist yet.
# Channels, project and people are variables (no real id, email or phone number here).

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "The environment's project (placeholder until the owner supplies it)."
}

variable "page_channels" {
  type        = list(string)
  description = "Notification channel ids for pages (D-60; the on-call tool is the owner's input)."
  default     = []
}

variable "ticket_channels" {
  type        = list(string)
  description = "Notification channel ids for tickets."
  default     = []
}

variable "workload_alerts_enabled" {
  type        = bool
  description = "Create the generated alerts on workload metrics (needs the OTLP exporter, V-OBS-1)."
  default     = false
}

variable "log_retention_days" {
  type        = number
  description = "Log bucket retention (TODO, needs product owner: no source gives it)."
  default     = 30
}

resource "google_logging_project_bucket_config" "app" {
  project        = var.project_id
  location       = "global"
  bucket_id      = "ai-app-logs"
  retention_days = var.log_retention_days
}

resource "google_logging_metric" "rls_denied" {
  project = var.project_id
  name    = "rls_denied_total"
  filter  = "jsonPayload.error_class=\"InsufficientPrivilege\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}
