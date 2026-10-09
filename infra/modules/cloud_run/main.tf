# Cloud Run services. api: ingress internal plus the load balancer, reached by the web server over
# direct VPC egress and by Cloud Tasks callbacks (OIDC; tasks identity only). web: public through
# the HTTPS load balancer only. Each runs as its own service account; secrets are Secret Manager
# references (never values); startup and liveness probes on /readyz and /healthz. The CI deploy
# rolls new image digests; Terraform owns everything else.

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
variable "subnet_id" {
  type = string
}
variable "services" {
  type = map(object({
    image         = string
    sa_email      = string
    port          = number
    ingress       = string
    env           = map(string)
    secret_env    = map(string)
    min_instances = number
    max_instances = number
    cpu           = string
    memory        = string
    timeout       = string
    probe_path    = string
    audiences     = list(string)
  }))
}
variable "public_invoker" {
  type        = list(string)
  description = "Services whose invoker is allUsers (application-level auth; ingress still limits)."
}
variable "invokers" {
  type        = map(map(string))
  description = "Service name to {workload name = service account email} granted run.invoker."
}

resource "google_cloud_run_v2_service" "svc" {
  for_each = var.services
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  project             = var.project_id
  name                = "${var.name}-${each.key}"
  location            = var.region
  ingress             = each.value.ingress
  deletion_protection = true
  # a fixed OIDC audience (Cloud Tasks callbacks) independent of the service URL
  custom_audiences = length(each.value.audiences) > 0 ? each.value.audiences : null
  template {
    service_account                  = each.value.sa_email
    timeout                          = each.value.timeout
    max_instance_request_concurrency = 40
    scaling {
      min_instance_count = each.value.min_instances
      max_instance_count = each.value.max_instances
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }
    containers {
      image = each.value.image
      ports {
        container_port = each.value.port
      }
      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
        cpu_idle = true
      }
      dynamic "env" {
        for_each = each.value.env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.value.secret_env
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
          path = each.value.probe_path
        }
        failure_threshold = 12
        period_seconds    = 5
      }
      liveness_probe {
        http_get {
          path = each.value.probe_path
        }
        period_seconds = 15
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = toset(var.public_invoker)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc[each.value].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "invoker" {
  for_each = merge([
    for svc, sas in var.invokers : { for who, sa in sas : "${svc}:${who}" => { svc = svc, sa = sa } }
  ]...)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.svc[each.value.svc].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value.sa}"
}

output "urls" {
  value = { for k, s in google_cloud_run_v2_service.svc : k => s.uri }
}
output "names" {
  value = { for k, s in google_cloud_run_v2_service.svc : k => s.name }
}
