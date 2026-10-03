# Cloud SQL for PostgreSQL 16: private IP only, IAM database authentication, automated backups with
# point-in-time recovery, CMEK. The runtime connects as an IAM service-account user (app_rw is
# granted to it by migration-time SQL; docs/runbooks/database.md). Tier and availability come from
# variables; no size or price is assumed here.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "network_id" {
  type = string
}
variable "private_services_connection" {
  type        = string
  description = "Dependency handle: Cloud SQL private IP needs the peering first."
}
variable "tier" {
  type        = string
  description = "Machine tier, chosen by the owner per environment (no default is assumed)."
}
variable "availability_type" {
  type    = string
  default = "ZONAL"
}
variable "kms_key_id" {
  type = string
}
variable "runtime_sa_email" {
  type = string
}
variable "deletion_protection" {
  type    = bool
  default = true
}

resource "google_sql_database_instance" "main" {
  project             = var.project_id
  name                = "${var.name}-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  encryption_key_name = var.kms_key_id
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.tier
    availability_type = var.availability_type
    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
      ssl_mode        = "ENCRYPTED_ONLY"
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    insights_config {
      query_insights_enabled = true
    }
  }

  depends_on = [var.private_services_connection]
}

resource "google_sql_database" "app" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = "ai"
}

resource "google_sql_user" "runtime" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = trimsuffix(var.runtime_sa_email, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}
output "database" {
  value = google_sql_database.app.name
}
output "iam_user" {
  value = google_sql_user.runtime.name
}
