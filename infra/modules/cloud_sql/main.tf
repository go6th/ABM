terraform {
  required_version = ">= 1.9"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
  }
}

# Cloud SQL for PostgreSQL 16 (pgvector is a built-in extension; migrations create it as the
# migrator): private IP only, TLS required, IAM database authentication, automated backups with
# point-in-time recovery, CMEK, deletion protection on both the API and the Terraform side.
# The api, worker and nightly-job accounts connect as IAM users and are granted the app_rw role
# (docs/deploy/RUNBOOK.md); the migrator is a separate built-in user whose URL only the migrate
# job's account can read. Tier, availability (HA) and max_connections are owner inputs.

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
variable "max_connections" {
  type        = number
  description = "Server max_connections (docs/deploy/ARCHITECTURE.md: instances x pool must stay below)."
}
variable "disk_size_gb" {
  type    = number
  default = 20
}
variable "labels" {
  type    = map(string)
  default = {}
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
  # checkov:skip=CKV_GCP_6:ssl_mode ENCRYPTED_ONLY refuses every unencrypted connection; client certificates are not used (the connector brings its own mTLS, the checkpointer uses sslmode=require)
  # checkov:skip=CKV_GCP_79:PostgreSQL 16 is the version every local, test and replica database runs; upgrade together
  # checkov:skip=CKV2_GCP_13:log_duration logs every statement; Query Insights records latency without that volume
  project             = var.project_id
  name                = "${var.name}-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  encryption_key_name = var.kms_key_id
  deletion_protection = true
  settings {
    # PostgreSQL 16 defaults to Enterprise Plus, which refuses db-custom tiers
    edition           = "ENTERPRISE"
    tier              = var.tier
    availability_type = var.availability_type
    disk_type         = "PD_SSD"
    disk_size         = var.disk_size_gb
    disk_autoresize   = true
    user_labels       = var.labels
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
    # static blocks (scanners read them): IAM auth, the connection ceiling, and connection, lock
    # and DDL diagnostics (CIS); statement text beyond DDL is never logged
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    database_flags {
      name  = "max_connections"
      value = tostring(var.max_connections)
    }
    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }
    database_flags {
      name  = "log_hostname"
      value = "on"
    }
    database_flags {
      name  = "log_temp_files"
      value = "0"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "-1"
    }
    database_flags {
      name  = "log_statement"
      value = "ddl"
    }
    database_flags {
      name  = "log_min_messages"
      value = "error"
    }
    database_flags {
      name  = "log_min_error_statement"
      value = "error"
    }
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
    insights_config {
      query_insights_enabled  = true
      record_application_tags = false
      record_client_address   = false
    }
    maintenance_window {
      day          = 7 # Sunday
      hour         = 3 # UTC
      update_track = "stable"
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
output "instance_name" {
  value = google_sql_database_instance.main.name
}
