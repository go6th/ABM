# Artifact Registry repository for the service image (one image serves api and worker).

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name}-images"
  format        = "DOCKER"
}

output "repository" {
  value = google_artifact_registry_repository.images.repository_id
}
