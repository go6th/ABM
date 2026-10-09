terraform {
  required_providers {
    google      = { source = "hashicorp/google" }
    google-beta = { source = "hashicorp/google-beta" }
  }
}

# Cloud SQL for PostgreSQL 16: private IP only, TLS required, IAM database authentication, automated
# backups with point-in-time recovery, CMEK, deletion protection. The api and job accounts connect
# as IAM users (granted the app_rw role by migration SQL; docs/runbooks/database.md). Tier and
# availability are owner inputs.

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
  description = "Dependency handle: private IP needs the service networking peering first."
}
variable "tier" {
  type = string
}
variable "availability_type" {
  type = string
  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.availability_type)
    error_message = "availability_type must be ZONAL or REGIONAL."
  }
}
variable "kms_key_id" {
  type = string
}
variable "iam_users" {
  type        = map(string)
  description = "Workload name to service account email connecting with IAM auth."
}
variable "backup_retention_days" {
  type = number
}

resource "google_project_service_identity" "sql" {
  provider = google-beta
  project  = var.project_id
  service  = "sqladmin.googleapis.com"
}

resource "google_kms_crypto_key_iam_member" "sql" {
  crypto_key_id = var.kms_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.sql.email}"
}

resource "google_sql_database_instance" "main" {
  project             = var.project_id
  name                = "${var.name}-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  encryption_key_name = var.kms_key_id
  deletion_protection = true
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
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = var.backup_retention_days
      }
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    insights_config {
      query_insights_enabled = true
    }
    deletion_protection_enabled = true
  }
  depends_on = [var.private_services_connection, google_kms_crypto_key_iam_member.sql]
}

resource "google_sql_database" "app" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = "ai"
}

resource "google_sql_user" "iam" {
  for_each = var.iam_users
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}
output "database" {
  value = google_sql_database.app.name
}
output "iam_users" {
  value = { for k, u in google_sql_user.iam : k => u.name }
}
