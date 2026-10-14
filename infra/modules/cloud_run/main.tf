# Cloud Run services (docs/deploy/ARCHITECTURE.md has the sizing arithmetic).
#   web     public only through the HTTPS load balancer (ingress internal-and-load-balancer).
#   api     ingress internal: reached by the web server over direct VPC egress; never by the
#           internet (the load balancer does not route to it).
#   worker  ingress internal and no public invoker: only the Cloud Tasks / Cloud Scheduler identity
#           holds run.invoker, and the application checks that identity's OIDC token again.
# Each runs as its own service account; secrets are Secret Manager references (never values). The
# CI deploy rolls new image digests and Cloud Run traffic; Terraform owns everything else, so a
# traffic rollback made with gcloud is not undone by the next apply.

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
variable "labels" {
  type    = map(string)
  default = {}
}
variable "services" {
  type = map(object({
    image         = string
    command       = optional(list(string), [])
    sa_email      = string
    port          = number
    ingress       = string
    env           = map(string)
    secret_env    = map(string)
    min_instances = number
    max_instances = number
    concurrency   = number
    cpu           = string
    memory        = string
    # false: CPU stays allocated between requests (background sweeper, work after the response)
    cpu_idle            = bool
    gen2                = optional(bool, false)
    timeout             = string
    startup_probe_path  = string
    liveness_probe_path = string
    audiences           = list(string)
    # mount path -> bucket, read only (Cloud Storage FUSE; needs gen2)
    gcs_mounts = optional(map(string), {})
    # mount directory -> { secret id, file name } (the latest version as a file)
    secret_files = optional(map(object({ secret = string, file = string })), {})
    # helper containers in the same instance (reached on localhost; no ingress port)
    sidecars = optional(map(object({
      image      = string
      cpu        = string
      memory     = string
      probe_port = number
      env        = optional(map(string), {})
    })), {})
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
    ignore_changes = [template[0].containers[0].image, client, client_version, traffic]
  }
  project             = var.project_id
  name                = "${var.name}-${each.key}"
  location            = var.region
  ingress             = each.value.ingress
  deletion_protection = true
  labels              = merge(var.labels, { service = each.key })
  # a fixed OIDC audience (Cloud Tasks callbacks) independent of the service URL
  custom_audiences = length(each.value.audiences) > 0 ? each.value.audiences : null
  template {
    service_account                  = each.value.sa_email
    timeout                          = each.value.timeout
    max_instance_request_concurrency = each.value.concurrency
    execution_environment            = each.value.gen2 ? "EXECUTION_ENVIRONMENT_GEN2" : "EXECUTION_ENVIRONMENT_GEN1"
    labels                           = merge(var.labels, { service = each.key })
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
    dynamic "volumes" {
      for_each = each.value.gcs_mounts
      content {
        name = "gcs-${substr(sha1(volumes.key), 0, 8)}"
        gcs {
          bucket    = volumes.value
          read_only = true
        }
      }
    }
    dynamic "volumes" {
      for_each = each.value.secret_files
      content {
        name = "secret-${substr(sha1(volumes.key), 0, 8)}"
        secret {
          secret = volumes.value.secret
          items {
            version = "latest"
            path    = volumes.value.file
          }
        }
      }
    }
    containers {
      name       = each.key
      image      = each.value.image
      command    = length(each.value.command) > 0 ? each.value.command : null
      depends_on = length(each.value.sidecars) > 0 ? keys(each.value.sidecars) : null
      ports {
        container_port = each.value.port
      }
      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
        cpu_idle          = each.value.cpu_idle
        startup_cpu_boost = true
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
      dynamic "volume_mounts" {
        for_each = each.value.gcs_mounts
        content {
          name       = "gcs-${substr(sha1(volume_mounts.key), 0, 8)}"
          mount_path = volume_mounts.key
        }
      }
      dynamic "volume_mounts" {
        for_each = each.value.secret_files
        content {
          name       = "secret-${substr(sha1(volume_mounts.key), 0, 8)}"
          mount_path = volume_mounts.key
        }
      }
      startup_probe {
        http_get {
          path = each.value.startup_probe_path
        }
        initial_delay_seconds = 0
        failure_threshold     = 24
        period_seconds        = 5
        timeout_seconds       = 3
      }
      liveness_probe {
        http_get {
          path = each.value.liveness_probe_path
        }
        period_seconds    = 15
        timeout_seconds   = 3
        failure_threshold = 3
      }
    }
    dynamic "containers" {
      for_each = each.value.sidecars
      content {
        name  = containers.key
        image = containers.value.image
        resources {
          limits = {
            cpu    = containers.value.cpu
            memory = containers.value.memory
          }
          cpu_idle = each.value.cpu_idle
        }
        dynamic "env" {
          for_each = containers.value.env
          content {
            name  = env.key
            value = env.value
          }
        }
        # the main container starts only after this answers on its port
        startup_probe {
          tcp_socket {
            port = containers.value.probe_port
          }
          initial_delay_seconds = 10
          period_seconds        = 10
          timeout_seconds       = 5
          failure_threshold     = 24 # signature download and load can take a few minutes
        }
        liveness_probe {
          tcp_socket {
            port = containers.value.probe_port
          }
          period_seconds    = 30
          timeout_seconds   = 5
          failure_threshold = 3
        }
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
