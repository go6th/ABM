terraform {
  required_providers {
    google      = { source = "hashicorp/google" }
    google-beta = { source = "hashicorp/google-beta" }
  }
}

# KMS (CMEK for Cloud SQL, the bucket and secrets) and the Secret Manager secrets. Secret VALUES are
# never in Terraform (so never in state): the owner adds versions out of band
# (docs/runbooks/secrets.md). Each secret is readable only by the workloads listed for it.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "secrets" {
  type        = map(list(string))
  description = "Secret id to the workload names allowed to read it (least privilege)."
}
variable "accounts" {
  type        = map(string)
  description = "Workload name to service account email (keys are static; emails resolve at apply)."
}

resource "google_kms_key_ring" "main" {
  project  = var.project_id
  name     = "${var.name}-keys"
  location = var.region
}

resource "google_kms_crypto_key" "data" {
  name            = "${var.name}-data"
  key_ring        = google_kms_key_ring.main.id
  rotation_period = "7776000s" # 90 days
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service_identity" "secretmanager" {
  provider = google-beta
  project  = var.project_id
  service  = "secretmanager.googleapis.com"
}

resource "google_kms_crypto_key_iam_member" "secretmanager" {
  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.secretmanager.email}"
}

resource "google_secret_manager_secret" "s" {
  for_each  = var.secrets
  project   = var.project_id
  secret_id = each.key
  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.data.id
        }
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.secretmanager]
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each = merge([
    for id, readers in var.secrets : { for r in readers : "${id}:${r}" => { id = id, who = r } }
  ]...)
  project   = var.project_id
  secret_id = google_secret_manager_secret.s[each.value.id].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.accounts[each.value.who]}"
}

output "kms_key_id" {
  value = google_kms_crypto_key.data.id
}
output "secret_ids" {
  value = { for k, s in google_secret_manager_secret.s : k => s.secret_id }
}
