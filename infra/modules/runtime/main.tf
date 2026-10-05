# Cloud Run services: api (public, behind the token check) and worker (private: only the Cloud
# Tasks identity may invoke it). One image, APP_ENV selects the production adapters; the process
# refuses to start with any hermetic adapter (assert_gcp_runtime, W4) and /readyz fails closed (W5).
# Egress goes through the VPC (direct VPC egress) and Cloud NAT.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "app_env" {
  type        = string
  description = "staging or production."
}
variable "image" {
  type        = string
  description = "Image reference built by CI (digest pinned)."
}
variable "runtime_sa_email" {
  type = string
}
variable "tasks_sa_email" {
  type = string
}
variable "network_id" {
  type = string
}
variable "subnet_id" {
  type = string
}
variable "env" {
  type        = map(string)
  description = "Non-secret settings: GCP_PROJECT, TASKS_QUEUE, PAYLOAD_BUCKET, CLOUD_SQL_INSTANCE..."
}
variable "secret_env" {
  type        = map(string)
  description = "Environment variable name to Secret Manager secret id (latest version)."
  default     = {}
}
variable "tasks_audience" {
  type        = string
  description = "Fixed OIDC audience for Cloud Tasks calls to the worker."
}
variable "worker_timeout" {
  type        = string
  default     = "300s"
  description = "Request timeout for one slice (240 s slice plus checkpoint headroom)."
}

locals {
  services = {
    api    = { ingress = "INGRESS_TRAFFIC_ALL", timeout = "60s" }
    worker = { ingress = "INGRESS_TRAFFIC_ALL", timeout = var.worker_timeout }
  }
}

resource "google_cloud_run_v2_service" "svc" {
  for_each = local.services
  # CI rolls out new images (gcloud run deploy); Terraform owns everything else.
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  project  = var.project_id
  name     = "${var.name}-${each.key}"
  location = var.region
  ingress  = each.value.ingress

  # The worker accepts OIDC tokens minted for a fixed audience (TASKS_AUDIENCE), so the audience
  # does not depend on the service URL.
  custom_audiences = each.key == "worker" ? [var.tasks_audience] : null

  template {
    service_account = var.runtime_sa_email
    timeout         = each.value.timeout
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }
    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      env {
        name  = "APP_ENV"
        value = var.app_env
      }
      env {
        name  = "SERVICE_ROLE"
        value = each.key
      }
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
        failure_threshold = 10
        period_seconds    = 5
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }
}

# Only the Cloud Tasks identity may call the worker (the endpoint also verifies the token, W3).
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc["worker"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.tasks_sa_email}"
}

# The api is reachable by clients; every business endpoint still requires a verified JWT.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc["api"].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "api_url" {
  value = google_cloud_run_v2_service.svc["api"].uri
}
output "worker_url" {
  value = google_cloud_run_v2_service.svc["worker"].uri
}
