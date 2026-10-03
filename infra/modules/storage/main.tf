# Raw provider payloads (neutral keys) in one private bucket: uniform access, public access
# prevented, versioning, CMEK. Retention follows decision D-06 (pending counsel), so no delete rule
# is set here; the owner sets it after the decision.

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
variable "runtime_sa_email" {
  type = string
}

resource "google_storage_bucket" "payloads" {
  project                     = var.project_id
  name                        = "${var.name}-payloads"
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
}

resource "google_storage_bucket_iam_member" "runtime_objects" {
  bucket = google_storage_bucket.payloads.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.runtime_sa_email}"
}

output "bucket" {
  value = google_storage_bucket.payloads.name
}
