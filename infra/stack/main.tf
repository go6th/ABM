# The whole environment, composed from the modules. infra/envs/{staging,prod} call this with their
# own values (no defaults for project, region, billing account or domain: they are owner inputs).
# Nothing here is applied by CI's quality gates: plan runs offline against mock providers
# (stack/tests), and apply happens only when the owner runs it (docs/deploy/RUNBOOK.md).
#
# Secrets: Terraform creates Secret Manager secret CONTAINERS and wires Cloud Run to them by
# reference; values are added out of band (docs/deploy/RUNBOOK.md), so no secret value is ever
# in an image, a Terraform variable or Terraform state. The one version Terraform writes is the
# Memorystore server CA certificate, which is public.
#
# Sizing (instances, CPU, memory, concurrency, timeouts, queue rates, database connections) is
# explained with its arithmetic in docs/deploy/ARCHITECTURE.md; change both together.

terraform {
  required_version = ">= 1.9"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
  }
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}
variable "project_id" {
  type = string
}
variable "region" {
  type        = string
  description = "Region of every regional resource (e.g. us-central1 or europe-west2)."
}
variable "billing_account" {
  type        = string
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX) for the budget."
}
variable "domain" {
  type        = string
  description = "Public hostname of the web app (DNS A record -> output web_ip)."
}
variable "github_repository" {
  type = string
}
variable "deploy_ref" {
  type = string
}
variable "images" {
  type        = object({ api = string, worker = string, web = string })
  description = "Initial image references by digest (CI rolls later digests)."
  validation {
    condition     = alltrue([for i in values(var.images) : can(regex("@sha256:[0-9a-f]{64}$", i))])
    error_message = "Every image must be pinned by @sha256 digest."
  }
}
variable "clamav_image" {
  type        = string
  description = "ClamAV image for the worker's clamd sidecar, mirrored into Artifact Registry and pinned by digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.clamav_image))
    error_message = "The ClamAV image must be pinned by @sha256 digest."
  }
}
variable "db_tier" {
  type = string
}
variable "db_availability" {
  type        = string
  description = "ZONAL, or REGIONAL for high availability (a standby in a second zone)."
}
variable "db_max_connections" {
  type        = number
  description = "Cloud SQL max_connections (docs/deploy/ARCHITECTURE.md, connection arithmetic)."
}
variable "redis_tier" {
  type        = string
  description = "Memorystore tier: BASIC or STANDARD_HA."
}
variable "llm_access" {
  type = string
  validation {
    condition     = contains(["anthropic_api", "vertex_ai"], var.llm_access)
    error_message = "llm_access must be anthropic_api or vertex_ai."
  }
}
variable "moonshot_enabled" {
  type        = bool
  description = "Offer Kimi K3: set only after the moonshot-api-key secret has a version (the services read it at start)."
  default     = false
}
variable "moonshot_api" {
  type = object({
    base_url = string
    dialect  = string
    model    = string
  })
  description = "Where Kimi K3 is served: Moonshot AI (default), or Ollama Cloud (base_url https://ollama.com/v1, dialect ollama, model kimi-k3:cloud). model empty sends kimi-k3."
  default     = { base_url = "https://api.moonshot.ai/v1", dialect = "moonshot", model = "" }
  validation {
    condition     = contains(["moonshot", "ollama"], var.moonshot_api.dialect) && startswith(var.moonshot_api.base_url, "https://")
    error_message = "moonshot_api.dialect must be moonshot or ollama, and base_url must be https."
  }
}
variable "maildrive" {
  type = object({
    google_client_id    = string
    microsoft_client_id = string
  })
  description = "Mail and drive OAuth client ids; a non-empty id wires its client secret (which must then have a version)."
  default     = { google_client_id = "", microsoft_client_id = "" }
}
variable "mail" {
  type = object({
    host      = string
    port      = optional(number, 587)
    username  = optional(string, "")
    from      = optional(string, "")
    from_name = optional(string, "")
  })
  description = "SMTP for invites, email verification, password reset and alert email; host \"\" keeps mail off. The smtp-password secret always exists; set host only once it has a version."
  default     = { host = "" }
}
variable "monthly_budget_usd" {
  type = number
}
variable "alert_channels" {
  type        = list(string)
  description = "Monitoring notification channel ids for budget, page and ticket alerts."
}
variable "raw_retention_days" {
  type = number
}
variable "staff" {
  type = object({
    workspace_domain = string
    operator_emails  = string
    support_emails   = string
    oauth_client_id  = string
    # personal Google accounts (no Workspace) allowed as staff; each still needs a role above
    external_emails = optional(string, "")
  })
  description = "Google Workspace staff console settings (non-secret)."
}
variable "scaling" {
  type = object({
    api    = object({ min = number, max = number })
    web    = object({ min = number, max = number })
    worker = object({ min = number, max = number })
  })
  description = "Cloud Run instance bounds per service (max x pool size feeds the database arithmetic)."
}
variable "archive_lock" {
  type        = bool
  description = "Lock the audit archive's 7-year retention policy (irreversible)."
  default     = false
}
variable "workload_alerts_enabled" {
  type        = bool
  description = "Create the generated alerts on application metrics (needs the OTLP exporter, V-OBS-1)."
  default     = false
}
variable "rate_limit_per_minute" {
  type        = number
  description = "Cloud Armor per-client request limit (one office behind one NAT address shares it)."
  default     = 1200
}

provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  name    = "ai-${var.environment}"
  app_env = var.environment == "prod" ? "production" : "staging"
  labels  = { app = "account-intelligence", env = var.environment, managed-by = "terraform" }
  # Cloud Run's deterministic URL (https://SERVICE-PROJECT_NUMBER.REGION.run.app) breaks the
  # service <-> environment URL cycle; the deploy checks it against the live service URL.
  run_url        = "${data.google_project.this.number}.${var.region}.run.app"
  api_url        = "https://${local.name}-api-${local.run_url}"
  worker_url     = "https://${local.name}-worker-${local.run_url}"
  web_origin     = "https://${var.domain}"
  tasks_audience = "${local.name}-worker-tasks"
  sa             = module.iam.emails

  # Per-instance shape (docs/deploy/ARCHITECTURE.md). The worker serves run slices (240 s each),
  # document parts (sandbox children of up to 1536 MiB, 1800 s) and short job tasks.
  size = {
    api    = { cpu = "1", memory = "1Gi", concurrency = 40, timeout = "300s" }
    web    = { cpu = "1", memory = "512Mi", concurrency = 40, timeout = "300s" }
    worker = { cpu = "4", memory = "8Gi", concurrency = 4, timeout = "3600s" }
  }
  # database pool per process the arithmetic assumes (Agent 4 sets the code side; handoff)
  pool = { size = 5, overflow = 5 }

  # Cloud Tasks: together the queues never dispatch more than the worker can hold at its maximum
  # (max instances x concurrency 4 = runs 2 + documents 1 + jobs 1 per instance).
  queues = {
    runs = {
      max_dispatches_per_second = 5
      max_concurrent_dispatches = var.scaling.worker.max * 2
      max_attempts              = 5
      min_backoff               = "10s"
      max_backoff               = "300s"
      max_doublings             = 4
    }
    documents = {
      max_dispatches_per_second = 2
      max_concurrent_dispatches = var.scaling.worker.max
      max_attempts              = 10 # "busy" (503) is a normal answer while a client's parts run
      min_backoff               = "30s"
      max_backoff               = "600s"
      max_doublings             = 4
    }
    jobs = {
      max_dispatches_per_second = 5
      max_concurrent_dispatches = var.scaling.worker.max
      max_attempts              = 5
      min_backoff               = "10s"
      max_backoff               = "300s"
      max_doublings             = 4
    }
  }

  # Secret id -> the workloads allowed to read it (least privilege). Values are owner inputs.
  secrets = merge(
    {
      "checkpoint-database-url"       = ["api", "worker", "job"]
      "migrator-database-url"         = ["migrate"]
      "app-rw-password"               = ["migrate"]
      "anthropic-api-key"             = ["api", "worker"] # read through the Secret Manager API
      "moonshot-api-key"              = ["api", "worker"] # read through the API when enabled
      "csrf-signing-key"              = ["api"]           # CSRF signing key, >= 32 random bytes
      "operator-google-client-secret" = ["web"]
      "p-search-api-key"              = ["worker"]
      "p-scrape-api-key"              = ["worker"]
      "p-signals-api-key"             = ["worker"]
      "p-signals-api-token"           = ["worker"]
      "p-people-api-key"              = ["worker"]
      "p-org-api-key"                 = ["worker"]
      "smtp-password"                 = ["api", "worker"] # SMTP password or app password (mail)
    },
    var.maildrive.google_client_id != "" ? { "maildrive-google-client-secret" = ["api", "worker"] } : {},
    var.maildrive.microsoft_client_id != "" ? { "maildrive-microsoft-client-secret" = ["api", "worker"] } : {},
  )
  redis_ca_dir = "/etc/redis-ca"
  common_env = merge(
    {
      APP_ENV            = local.app_env
      LOG_LEVEL          = "INFO"
      GCP_PROJECT        = var.project_id
      GCP_REGION         = var.region
      CLOUD_SQL_INSTANCE = module.cloud_sql.connection_name
      DB_NAME            = module.cloud_sql.database
      DB_POOL_SIZE       = tostring(local.pool.size)
      DB_MAX_OVERFLOW    = tostring(local.pool.overflow)
      PAYLOAD_BUCKET     = module.gcs.bucket
      # TLS to Memorystore against its own CA (mounted file); VERIFY(V-REDIS-1) hostname check
      REDIS_URL = "rediss://${module.cache.host}:${module.cache.port}/0?ssl_ca_certs=${local.redis_ca_dir}/ca.pem&ssl_check_hostname=false"
      # the python-docx renderer until the Node library is ported (FIX_LOG D10)
      EXPORT_RENDERER       = "python-interim"
      PUBLIC_BASE_URL       = local.web_origin
      JWKS_URL              = module.identity_platform.jwks_url
      JWT_ISSUER            = module.identity_platform.issuer
      JWT_AUDIENCE          = module.identity_platform.audience
      LLM_ACCESS            = var.llm_access
      VERTEX_REGION         = var.region
      TASKS_QUEUE           = module.tasks.queues["runs"]
      TASKS_QUEUE_DOCUMENTS = module.tasks.queues["documents"] # read once the handoff lands
      TASKS_QUEUE_JOBS      = module.tasks.queues["jobs"]      # read once the handoff lands
      WORKER_URL            = local.worker_url
      TASKS_SERVICE_ACCOUNT = local.sa.tasks
      TASKS_AUDIENCE        = local.tasks_audience
      BYOK_KMS_KEY          = google_kms_crypto_key.byok.id
    },
    var.moonshot_enabled ? {
      MOONSHOT_SECRET      = "moonshot-api-key"
      MOONSHOT_BASE_URL    = var.moonshot_api.base_url
      MOONSHOT_API_DIALECT = var.moonshot_api.dialect
    } : {},
    var.moonshot_enabled && var.moonshot_api.model != "" ? { MOONSHOT_MODEL = var.moonshot_api.model } : {},
    var.maildrive.google_client_id != "" ? { MAILDRIVE_GOOGLE_CLIENT_ID = var.maildrive.google_client_id } : {},
    var.maildrive.microsoft_client_id != "" ? { MAILDRIVE_MICROSOFT_CLIENT_ID = var.maildrive.microsoft_client_id } : {},
    var.mail.host != "" ? {
      SMTP_HOST      = var.mail.host
      SMTP_PORT      = tostring(var.mail.port)
      SMTP_USERNAME  = var.mail.username
      MAIL_FROM      = var.mail.from
      MAIL_FROM_NAME = var.mail.from_name
    } : {},
  )
  db_secret = { CHECKPOINT_DATABASE_URL = "checkpoint-database-url" }
  maildrive_secret = merge(
    var.maildrive.google_client_id != "" ? { MAILDRIVE_GOOGLE_CLIENT_SECRET = "maildrive-google-client-secret" } : {},
    var.maildrive.microsoft_client_id != "" ? { MAILDRIVE_MICROSOFT_CLIENT_SECRET = "maildrive-microsoft-client-secret" } : {},
    var.mail.host != "" ? { SMTP_PASSWORD = "smtp-password" } : {},
  )
  providers_secret = {
    P_SEARCH_API_KEY    = "p-search-api-key"
    P_SCRAPE_API_KEY    = "p-scrape-api-key"
    P_SIGNALS_API_KEY   = "p-signals-api-key"
    P_SIGNALS_API_TOKEN = "p-signals-api-token"
    P_PEOPLE_API_KEY    = "p-people-api-key"
    P_ORG_API_KEY       = "p-org-api-key"
  }
  redis_ca_file = { (local.redis_ca_dir) = { secret = google_secret_manager_secret.redis_ca.secret_id, file = "ca.pem" } }
  blob_mount    = "/mnt/blobs"
}

module "project" {
  source     = "../modules/project"
  project_id = var.project_id
}

module "iam" {
  source             = "../modules/iam"
  project_id         = var.project_id
  name               = local.name
  github_repository  = var.github_repository
  deploy_ref         = var.deploy_ref
  github_environment = var.environment == "prod" ? "production" : "staging"
  vertex_ai          = var.llm_access == "vertex_ai"
  depends_on         = [module.project]
}

module "network" {
  source     = "../modules/network"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  depends_on = [module.project]
}

module "secret_manager" {
  source     = "../modules/secret_manager"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  secrets    = local.secrets
  accounts   = module.iam.emails
  depends_on = [module.project]
}

# Optional per-tenant provider keys are KMS encrypted by the API and decrypted for its chat calls.
resource "google_kms_crypto_key" "byok" {
  name            = "${local.name}-byok"
  key_ring        = "projects/${var.project_id}/locations/${var.region}/keyRings/${local.name}-keys"
  rotation_period = "7776000s"
  labels          = local.labels
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [module.secret_manager]
}

resource "google_kms_crypto_key_iam_member" "byok_encrypt" {
  crypto_key_id = google_kms_crypto_key.byok.id
  role          = "roles/cloudkms.cryptoKeyEncrypter"
  member        = "serviceAccount:${local.sa.api}"
}

resource "google_kms_crypto_key_iam_member" "byok_decrypt" {
  crypto_key_id = google_kms_crypto_key.byok.id
  role          = "roles/cloudkms.cryptoKeyDecrypter"
  member        = "serviceAccount:${local.sa.api}"
}

module "cloud_sql" {
  source                      = "../modules/cloud_sql"
  project_id                  = var.project_id
  region                      = var.region
  name                        = local.name
  network_id                  = module.network.network_id
  private_services_connection = module.network.private_services_connection
  tier                        = var.db_tier
  availability_type           = var.db_availability
  max_connections             = var.db_max_connections
  kms_key_id                  = module.secret_manager.kms_key_id
  iam_users                   = { api = local.sa.api, worker = local.sa.worker, job = local.sa.job }
  backup_retention_days       = var.environment == "prod" ? 30 : 7
  labels                      = local.labels
}

module "gcs" {
  source             = "../modules/gcs"
  project_id         = var.project_id
  region             = var.region
  name               = local.name
  kms_key_id         = module.secret_manager.kms_key_id
  object_users       = { api = local.sa.api, worker = local.sa.worker }
  raw_retention_days = var.raw_retention_days
  web_origin         = local.web_origin
  archive_writers    = { worker = local.sa.worker }
  archive_lock       = var.archive_lock
  labels             = local.labels
}

module "cache" {
  source                      = "../modules/cache"
  project_id                  = var.project_id
  region                      = var.region
  name                        = local.name
  network_id                  = module.network.network_id
  private_services_connection = module.network.private_services_connection
  tier                        = var.redis_tier
  labels                      = local.labels
}

# The Memorystore server CA (public) as a file the services mount, so TLS verifies the instance.
resource "google_secret_manager_secret" "redis_ca" {
  project   = var.project_id
  secret_id = "redis-server-ca"
  labels    = local.labels
  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = module.secret_manager.kms_key_id
        }
      }
    }
  }
  depends_on = [module.secret_manager]
}

resource "google_secret_manager_secret_version" "redis_ca" {
  secret      = google_secret_manager_secret.redis_ca.id
  secret_data = module.cache.server_ca_pem
}

resource "google_secret_manager_secret_iam_member" "redis_ca" {
  for_each  = toset(["api", "worker"])
  project   = var.project_id
  secret_id = google_secret_manager_secret.redis_ca.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.sa[each.value]}"
}

module "tasks" {
  source     = "../modules/tasks"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  queues     = local.queues
  depends_on = [module.project]
}

module "registry" {
  source     = "../modules/registry"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  labels     = local.labels
  depends_on = [module.project]
}

module "identity_platform" {
  source             = "../modules/identity_platform"
  project_id         = var.project_id
  authorized_domains = [var.domain]
  depends_on         = [module.project]
}

module "cloud_run" {
  source     = "../modules/cloud_run"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  network_id = module.network.network_id
  subnet_id  = module.network.subnet_id
  labels     = local.labels
  services = {
    api = {
      image    = var.images.api
      sa_email = local.sa.api
      port     = 8080
      # internal: only the web server reaches it (direct VPC egress); the load balancer does not
      ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"
      env = merge(local.common_env, {
        SERVICE_ROLE           = "api"
        DB_IAM_USER            = module.cloud_sql.iam_users["api"]
        CSRF_KEY_SECRET        = "csrf-signing-key" # the secret id the api reads (audit M5/M6)
        STAFF_JWT_ISSUER       = "https://accounts.google.com"
        STAFF_JWT_AUDIENCE     = var.staff.oauth_client_id
        STAFF_JWKS_URL         = "https://www.googleapis.com/oauth2/v3/certs"
        STAFF_WORKSPACE_DOMAIN = var.staff.workspace_domain
        STAFF_OPERATOR_EMAILS  = var.staff.operator_emails
        STAFF_SUPPORT_EMAILS   = var.staff.support_emails
        STAFF_EXTERNAL_EMAILS  = var.staff.external_emails
      })
      secret_env          = merge(local.db_secret, local.maildrive_secret)
      secret_files        = local.redis_ca_file
      min_instances       = var.scaling.api.min
      max_instances       = var.scaling.api.max
      concurrency         = local.size.api.concurrency
      cpu                 = local.size.api.cpu
      memory              = local.size.api.memory
      cpu_idle            = true
      timeout             = local.size.api.timeout
      startup_probe_path  = "/readyz"
      liveness_probe_path = "/healthz"
      audiences           = []
    }
    worker = {
      image = var.images.worker
      # the worker image's entry point runs one CLI command; as a service it serves the same
      # application as the api with SERVICE_ROLE=worker (the /tasks/* routes and the sweeper)
      command = ["uvicorn", "account_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8080",
      "--workers", "1", "--timeout-graceful-shutdown", "8", "--no-access-log"]
      sa_email = local.sa.worker
      port     = 8080
      ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY" # Cloud Tasks and Cloud Scheduler count as internal
      env = merge(local.common_env, {
        SERVICE_ROLE     = "worker"
        DB_IAM_USER      = module.cloud_sql.iam_users["worker"]
        DOCUMENT_SCANNER = "clamd"
        CLAMD_HOST       = "127.0.0.1"
        CLAMD_PORT       = "3310"
        BLOB_MOUNT_DIR   = local.blob_mount # read once the handoff lands (no full-file temp copy)
      })
      secret_env    = merge(local.db_secret, local.providers_secret, local.maildrive_secret)
      secret_files  = local.redis_ca_file
      gcs_mounts    = { (local.blob_mount) = module.gcs.bucket }
      min_instances = var.scaling.worker.min
      max_instances = var.scaling.worker.max
      concurrency   = local.size.worker.concurrency
      cpu           = local.size.worker.cpu
      memory        = local.size.worker.memory
      # CPU stays on between requests: the orphan and stall sweeper runs in the background
      cpu_idle            = false
      gen2                = true
      timeout             = local.size.worker.timeout
      startup_probe_path  = "/readyz"
      liveness_probe_path = "/healthz"
      audiences           = [local.tasks_audience]
      sidecars = {
        clamd = {
          image      = var.clamav_image
          cpu        = "1"
          memory     = "4Gi" # signatures in memory, twice during a reload
          probe_port = 3310
        }
      }
    }
    web = {
      image    = var.images.web
      sa_email = local.sa.web
      port     = 3000
      ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
      env = {
        API_ORIGIN                   = local.api_url
        UPLOAD_ORIGIN                = "https://storage.googleapis.com"
        STAFF_WORKSPACE_DOMAIN       = var.staff.workspace_domain
        STAFF_EXTERNAL_EMAILS        = var.staff.external_emails
        OPERATOR_GOOGLE_CLIENT_ID    = var.staff.oauth_client_id
        OPERATOR_GOOGLE_REDIRECT_URI = "${local.web_origin}/api/operator/callback"
      }
      secret_env          = { OPERATOR_GOOGLE_CLIENT_SECRET = "operator-google-client-secret" }
      min_instances       = var.scaling.web.min
      max_instances       = var.scaling.web.max
      concurrency         = local.size.web.concurrency
      cpu                 = local.size.web.cpu
      memory              = local.size.web.memory
      cpu_idle            = true
      timeout             = local.size.web.timeout
      startup_probe_path  = "/healthz"
      liveness_probe_path = "/healthz"
      audiences           = []
    }
  }
  # web: behind the load balancer (a serverless NEG needs allUsers); api: internal ingress, and
  # it authenticates every business request itself (the web server forwards the session cookie)
  public_invoker = ["api", "web"]
  # worker: only the tasks identity (Cloud Tasks and Cloud Scheduler sign as it)
  invokers   = { worker = { tasks = local.sa.tasks } }
  depends_on = [module.secret_manager, google_secret_manager_secret_version.redis_ca]
}

module "cloud_run_jobs" {
  source     = "../modules/cloud_run_jobs"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  network_id = module.network.network_id
  subnet_id  = module.network.subnet_id
  labels     = local.labels
  jobs = {
    spend-invariants = {
      image       = var.images.worker
      sa_email    = local.sa.job
      command     = ["python", "-m", "account_intelligence.worker", "spend-invariants"]
      args        = []
      env         = merge(local.common_env, { SERVICE_ROLE = "job", DB_IAM_USER = module.cloud_sql.iam_users["job"] })
      secret_env  = local.db_secret
      timeout     = "1800s"
      max_retries = 1
      cpu         = "1"
      memory      = "1Gi"
    }
    migrate = {
      image    = var.images.api
      sa_email = local.sa.migrate
      command  = ["alembic", "upgrade", "head"]
      args     = []
      env = {
        APP_ENV      = local.app_env
        SERVICE_ROLE = "migrate"
        # the IAM database users to be members of app_rw and nothing else (audit L16; read once
        # the migration handoff lands; until then the runbook grants it by hand)
        APP_RW_IAM_USERS = join(",", values(module.cloud_sql.iam_users))
      }
      secret_env  = { MIGRATOR_DATABASE_URL = "migrator-database-url", APP_RW_PASSWORD = "app-rw-password" }
      timeout     = "1800s"
      max_retries = 0
      cpu         = "1"
      memory      = "1Gi"
    }
  }
  runners = {
    spend-invariants = { scheduler = local.sa.scheduler }
    migrate          = { deployer = local.sa.deployer }
  }
  depends_on = [module.secret_manager]
}

module "scheduler" {
  source        = "../modules/scheduler"
  project_id    = var.project_id
  region        = var.region
  name          = local.name
  sa_email      = local.sa.scheduler
  time_zone     = "Etc/UTC"
  worker_url    = local.worker_url
  tick_sa_email = local.sa.tasks
  tick_audience = local.tasks_audience
  jobs = {
    spend-invariants = { schedule = "17 3 * * *", job_name = module.cloud_run_jobs.names["spend-invariants"] }
  }
  # routes that exist today run; Phase 5 routes are created paused (applies on deploy)
  http_ticks = {
    schedules-tick  = { schedule = "*/15 * * * *", path = "/tasks/schedules-tick", deadline = "300s", paused = false }
    isolation-check = { schedule = "0 2 * * *", path = "/tasks/isolation-check", deadline = "1800s", paused = false }
    access-expiry   = { schedule = "30 3 * * *", path = "/tasks/access-expiry", deadline = "900s", paused = false }
    maildrive-sync  = { schedule = "5 * * * *", path = "/tasks/maildrive-sync", deadline = "900s", paused = false }
    sweep           = { schedule = "*/5 * * * *", path = "/tasks/sweep", deadline = "300s", paused = true }
    retention       = { schedule = "45 3 * * *", path = "/tasks/retention", deadline = "1800s", paused = true }
    alert-digest    = { schedule = "0 7 * * *", path = "/tasks/alert-digest", deadline = "900s", paused = true }
    audit-verify    = { schedule = "15 4 * * *", path = "/tasks/audit-verify", deadline = "1800s", paused = true }
  }
  depends_on = [module.cloud_run]
}

module "edge" {
  source                = "../modules/edge"
  project_id            = var.project_id
  region                = var.region
  name                  = local.name
  domain                = var.domain
  web_service_name      = module.cloud_run.names["web"]
  rate_limit_per_minute = var.rate_limit_per_minute
}

module "observability" {
  source                  = "../modules/observability"
  project_id              = var.project_id
  name                    = local.name
  domain                  = var.domain
  page_channels           = var.alert_channels
  ticket_channels         = var.alert_channels
  workload_alerts_enabled = var.workload_alerts_enabled
  services = {
    web    = module.cloud_run.names["web"]
    api    = module.cloud_run.names["api"]
    worker = module.cloud_run.names["worker"]
  }
  queues              = module.tasks.queues
  sql_instance        = module.cloud_sql.instance_name
  sql_max_connections = var.db_max_connections
  migrate_job         = module.cloud_run_jobs.names["migrate"]
  uptime_period       = var.environment == "prod" ? "60s" : "300s"
  depends_on          = [module.project]
}

module "budget" {
  source                = "../modules/budget"
  billing_account       = var.billing_account
  project_number        = data.google_project.this.number
  name                  = local.name
  monthly_budget_usd    = var.monthly_budget_usd
  notification_channels = var.alert_channels
}

output "web_ip" {
  value = module.edge.ip_address
}
output "nat_ip" {
  value       = module.network.nat_ip
  description = "Fixed egress address for provider allow-lists."
}
output "wif_provider" {
  value = module.iam.wif_provider
}
output "deployer_email" {
  value = module.iam.emails.deployer
}
output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${module.registry.repository}"
}
output "services" {
  value = module.cloud_run.names
}
output "jobs" {
  value = module.cloud_run_jobs.names
}
output "queues" {
  value = module.tasks.queues
}
output "api_url" {
  value = local.api_url
}
output "worker_url" {
  value = local.worker_url
}
output "buckets" {
  value = { data = module.gcs.bucket, audit_archive = module.gcs.archive_bucket }
}
output "sql_connection_name" {
  value = module.cloud_sql.connection_name
}
output "service_accounts" {
  value = module.iam.emails
}
