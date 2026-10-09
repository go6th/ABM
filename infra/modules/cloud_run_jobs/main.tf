# Cloud Run Jobs.
#   run              one account run per execution: the api starts an execution with overridden
#                    arguments (--tenant-id, --run-id); the container drives the run's slices to a
#                    stop and exits. One task, bounded retries (the checkpoint resumes a retried
#                    execution), a timeout above the 4 h slice ceiling.
#   migrate          `alembic upgrade head`; executed by the deploy pipeline before any rollout.
#   spend-invariants the nightly invariant pass (started by Cloud Scheduler).
# Starting an execution with overrides needs run.jobs.runWithOverrides: granted per job
# (roles/run.developer on that job only), never project-wide.

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
variable "jobs" {
  type = map(object({
    image       = string
    sa_email    = string
    command     = list(string)
    args        = list(string)
    env         = map(string)
    secret_env  = map(string)
    timeout     = string
    max_retries = number
    cpu         = string
    memory      = string
  }))
}
variable "runners" {
  type        = map(map(string))
  description = "Job name to {workload name = service account email} that may start executions."
}

resource "google_cloud_run_v2_job" "job" {
  for_each = var.jobs
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  project             = var.project_id
  name                = "${var.name}-${each.key}"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = each.value.sa_email
      timeout         = each.value.timeout
      max_retries     = each.value.max_retries
      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = var.network_id
          subnetwork = var.subnet_id
        }
      }
      containers {
        image   = each.value.image
        command = length(each.value.command) > 0 ? each.value.command : null
        args    = each.value.args
        resources {
          limits = {
            cpu    = each.value.cpu
            memory = each.value.memory
          }
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
      }
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "runner" {
  for_each = merge([
    for job, sas in var.runners : { for who, sa in sas : "${job}:${who}" => { job = job, sa = sa } }
  ]...)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.job[each.value.job].name
  role     = "roles/run.developer"
  member   = "serviceAccount:${each.value.sa}"
}

output "names" {
  value = { for k, j in google_cloud_run_v2_job.job : k => j.name }
}
