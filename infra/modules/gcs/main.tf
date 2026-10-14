# One private data bucket per environment. Objects are laid out per tenant and the application only
# ever builds keys under its verified tenant (tenants/{tenant}/...). Uniform access, public access
# prevented, versioning plus soft delete (the existing choice: a deleted or overwritten object is
# recoverable for up to 7 + 7 days), CMEK.
#
# Lifecycle:
#   raw collection files      tenants/**/*.raw.json(.gz) expire after raw_retention_days
#   quarantine                quarantine/** (documents not yet scanned or rejected) after 7 days
#   upload parts              uploads/** (parts of an upload not yet composed) after 3 days
#   abandoned XML multipart   aborted after 2 days
# The quarantine and upload rules match top-level prefixes: they take effect once the application
# writes those keys under quarantine/ and uploads/ (handoff in docs/deploy/agents/gcp-infra.md;
# today they live under tenants/{tenant}/..., which a lifecycle prefix cannot match). A browser
# resumable session that is never finished creates no object and expires on its own after a week.
#
# CORS: the browser sends upload chunks straight to the bucket (research_files.blobstore, VERIFY
# V-FILES-1): PUT from the web origin only, with Content-Range, Range and Content-Type allowed and
# exposed (a 308 answer carries the committed Range).
#
# The audit archive bucket (FIX_PROMPT 5.6, "applies on deploy"): 7-year retention policy, locked
# only when the owner sets archive_lock = true (locking is irreversible).

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
variable "web_origin" {
  type        = string
  description = "The web app's origin (https://domain): the only origin allowed to upload."
}
variable "archive_writers" {
  type        = map(string)
  description = "Workload name to service account email that may create (never delete) archive objects."
}
variable "archive_lock" {
  type        = bool
  description = "Lock the archive retention policy (irreversible: the bucket cannot be deleted for 7 years)."
}
variable "labels" {
  type    = map(string)
  default = {}
}

locals {
  archive_retention_s = 220898664 # 7 x 365.25 days
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
  # checkov:skip=CKV_GCP_62:object access is recorded by Cloud Audit Logs (docs/deploy/ARCHITECTURE.md); usage-log buckets add cost and a second copy of key names
  project                     = var.project_id
  name                        = "${var.name}-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels
  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = var.kms_key_id
  }
  soft_delete_policy {
    retention_duration_seconds = 604800 # 7 days
  }
  cors {
    origin          = [var.web_origin]
    method          = ["PUT", "POST", "GET", "HEAD"]
    response_header = ["Content-Type", "Content-Range", "Range"]
    max_age_seconds = 3600
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
      age            = 7
      matches_prefix = ["quarantine/"]
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      age            = 3
      matches_prefix = ["uploads/"]
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      age = 2
    }
    action {
      type = "AbortIncompleteMultipartUpload"
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

resource "google_storage_bucket" "audit_archive" {
  # checkov:skip=CKV_GCP_62:object access is recorded by Cloud Audit Logs
  # checkov:skip=CKV_GCP_78:a bucket retention policy and object versioning are mutually exclusive; the retention lock is the protection
  project                     = var.project_id
  name                        = "${var.name}-audit-archive"
  location                    = var.region
  storage_class               = "ARCHIVE"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = merge(var.labels, { purpose = "audit-archive" })
  # a retention policy and object versioning are mutually exclusive; the policy is the protection
  versioning {
    enabled = false
  }
  encryption {
    default_kms_key_name = var.kms_key_id
  }
  retention_policy {
    retention_period = local.archive_retention_s
    is_locked        = var.archive_lock
  }
  lifecycle_rule {
    condition {
      age = 2558 # one day past the retention period
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_storage_bucket_iam_member" "archive_writer" {
  for_each = var.archive_writers
  bucket   = google_storage_bucket.audit_archive.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${each.value}"
}

output "bucket" {
  value = google_storage_bucket.data.name
}
output "archive_bucket" {
  value = google_storage_bucket.audit_archive.name
}
