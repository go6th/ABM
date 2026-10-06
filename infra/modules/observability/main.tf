# Observability module (D-56): log sinks, log-based metrics, dashboards, alert policies and uptime
# checks for one environment. Generated resources live in generated_*.tf.json (from
# config/telemetry by scripts/gen_observability.py); channels, project and people are variables
# (placeholders: no real id, email or phone number). Nothing here is applied by the build.

terraform {
  required_version = ">= 1.6"
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
