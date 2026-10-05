# KMS key ring and key (CMEK for Cloud SQL, the bucket and secrets) and the Secret Manager secrets
# the runtime reads. Secret VALUES are never in Terraform: the owner adds versions
# (docs/runbooks/secrets.md).

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "secret_ids" {
  type        = list(string)
  description = "Secret names (provider API keys, the Anthropic key when LLM_ACCESS=anthropic_api)."
}
variable "runtime_sa_email" {
  type = string
}

resource "google_kms_key_ring" "main" {
  project  = var.project_id
  name     = "${var.name}-keys"
  location = var.region
}

resource "google_kms_crypto_key" "data" {
  name            = "${var.name}-data"
  key_ring        = google_kms_key_ring.main.id
  rotation_period = "7776000s"
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret" "runtime" {
  for_each  = toset(var.secret_ids)
  project   = var.project_id
  secret_id = each.value
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  for_each  = google_secret_manager_secret.runtime
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.runtime_sa_email}"
}

output "kms_key_id" {
  value = google_kms_crypto_key.data.id
}
output "secret_ids" {
  value = [for s in google_secret_manager_secret.runtime : s.secret_id]
}
