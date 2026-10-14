# The data bucket is private, uploads are allowed from the web origin only, and the audit archive
# keeps objects for 7 years (locked only on the owner's word).
mock_provider "google" {
  mock_data "google_storage_project_service_account" {
    defaults = { email_address = "service-1@gs-project-accounts.iam.gserviceaccount.com" }
  }
}

variables {
  project_id         = "synthetic"
  region             = "us-central1"
  name               = "ai-staging"
  kms_key_id         = "projects/synthetic/locations/us-central1/keyRings/k/cryptoKeys/data"
  object_users       = { api = "api@x.iam.gserviceaccount.com", worker = "worker@x.iam.gserviceaccount.com" }
  raw_retention_days = 30
  web_origin         = "https://app.synthetic.test"
  archive_writers    = { worker = "worker@x.iam.gserviceaccount.com" }
  archive_lock       = false
}

run "data_bucket" {
  command = plan
  assert {
    condition     = google_storage_bucket.data.public_access_prevention == "enforced" && google_storage_bucket.data.uniform_bucket_level_access
    error_message = "private, uniform access"
  }
  assert {
    condition     = google_storage_bucket.data.cors[0].origin == tolist(["https://app.synthetic.test"])
    error_message = "CORS for the web origin only"
  }
  assert {
    condition     = toset(google_storage_bucket.data.cors[0].response_header) == toset(["Content-Type", "Content-Range", "Range"])
    error_message = "resumable upload headers"
  }
  assert {
    condition     = toset(flatten([for r in google_storage_bucket.data.lifecycle_rule : [for c in r.condition : coalesce(c.matches_prefix, [])]])) == toset(["tenants/", "quarantine/", "uploads/"])
    error_message = "lifecycle for raw files, quarantine and upload parts"
  }
  assert {
    condition     = google_storage_bucket.data.soft_delete_policy[0].retention_duration_seconds == 604800 && google_storage_bucket.data.versioning[0].enabled
    error_message = "soft delete and versioning"
  }
}

run "audit_archive" {
  command = plan
  assert {
    condition     = google_storage_bucket.audit_archive.retention_policy[0].retention_period == "220898664" && !google_storage_bucket.audit_archive.retention_policy[0].is_locked
    error_message = "7-year retention, unlocked until the owner decides"
  }
  assert {
    condition     = google_storage_bucket_iam_member.archive_writer["worker"].role == "roles/storage.objectCreator"
    error_message = "archive writers can create, never delete"
  }
}
