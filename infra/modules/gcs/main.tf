# One private data bucket. Objects are laid out per tenant and the application only ever builds keys
# under its verified tenant: tenants/{tenant}/runs/{run_id}/raw/... (collection output),
# tenants/{tenant}/runs/{run_id}/outputs/... (deliverables). Uniform access, public access
# prevented, versioning, CMEK, soft delete; raw collection files expire after raw_retention_days.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "kms_key_id" {
  type = string
}
variable "object_users" {
  type        = map(string)
  description = "Workload name to service account email that reads and writes objects."
}
variable "raw_retention_days" {
  type        = number
  description = "Days raw collection files are kept (owner and counsel decision)."
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

resource "google_kms_crypto_key_iam_member" "gcs" {
  crypto_key_id = var.kms_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "data" {
  project                     = var.project_id
  name                        = "${var.name}-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = var.kms_key_id
  }
  soft_delete_policy {
    retention_duration_seconds = 604800 # 7 days
  }
  lifecycle_rule {
    condition {
      age            = var.raw_retention_days
      matches_prefix = ["tenants/"]
      matches_suffix = [".raw.json", ".raw.json.gz"]
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 7
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_storage_bucket_iam_member" "objects" {
  for_each = var.object_users
  bucket   = google_storage_bucket.data.name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${each.value}"
}

output "bucket" {
  value = google_storage_bucket.data.name
}
