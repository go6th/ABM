# Artifact Registry repository for the images (api, worker, web), pushed by CI by digest. Cleanup
# keeps the 30 most recent versions of each image (so a rollback target is always there) and
# deletes versions older than 90 days beyond those; untagged layers older than 14 days go too.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "labels" {
  type    = map(string)
  default = {}
}

resource "google_artifact_registry_repository" "images" {
  # checkov:skip=CKV_GCP_84:Artifact Registry offers CMEK, not CSEK; images hold no tenant data or secret and are signed and scanned in CI
  project                = var.project_id
  location               = var.region
  repository_id          = "${var.name}-images"
  format                 = "DOCKER"
  labels                 = var.labels
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 30
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "7776000s" # 90 days
    }
  }
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "1209600s" # 14 days
    }
  }
}

output "repository" {
  value = google_artifact_registry_repository.images.repository_id
}
