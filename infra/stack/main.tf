# The whole environment, composed from the modules. infra/envs/{staging,prod} call this with their
# own values (no defaults for project, region, billing account or domain: they are owner inputs).
# Nothing here is applied by CI's quality gates: plan runs offline against mock providers
# (stack/tests), and apply happens only in the deploy workflows behind protected environments.
#
# Secrets: Terraform creates Secret Manager secret CONTAINERS and wires Cloud Run to them by
# reference; values are added out of band (docs/runbooks/secrets.md), so no secret value is ever
# in an image, a Terraform variable or Terraform state.

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
  type = string
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
variable "db_tier" {
  type = string
}
variable "db_availability" {
  type = string
}
variable "llm_access" {
  type = string
  validation {
    condition     = contains(["anthropic_api", "vertex_ai"], var.llm_access)
    error_message = "llm_access must be anthropic_api or vertex_ai."
  }
}
variable "monthly_budget_usd" {
  type = number
}
variable "alert_channels" {
  type        = list(string)
  description = "Monitoring notification channel ids for budget alerts."
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
  })
  description = "Google Workspace staff console settings (non-secret)."
}
variable "min_instances" {
  type = object({ api = number, web = number })
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
  # Cloud Run's deterministic URL (https://SERVICE-PROJECT_NUMBER.REGION.run.app) breaks the
  # api <-> web URL cycle; the deploy workflow checks it against the live service URL.
  api_url        = "https://${local.name}-api-${data.google_project.this.number}.${var.region}.run.app"
  tasks_audience = "${local.name}-api-tasks"
  sa             = module.iam.emails
  secrets = {
    "checkpoint-database-url"       = ["api", "job"]
    "migrator-database-url"         = ["migrate"]
    "app-rw-password"               = ["migrate"]
    "anthropic-api-key"             = ["api", "job"]
    "operator-google-client-secret" = ["web"]
    "p-search-api-key"              = ["job"]
    "p-scrape-api-key"              = ["job"]
    "p-signals-api-key"             = ["job"]
    "p-signals-api-token"           = ["job"]
    "p-people-api-key"              = ["job"]
    "p-org-api-key"                 = ["job"]
  }
  common_env = {
    APP_ENV               = local.app_env
    LOG_LEVEL             = "INFO"
    GCP_PROJECT           = var.project_id
    GCP_REGION            = var.region
    CLOUD_SQL_INSTANCE    = module.cloud_sql.connection_name
    DB_NAME               = module.cloud_sql.database
    PAYLOAD_BUCKET        = module.gcs.bucket
    REDIS_URL             = "rediss://${module.cache.host}:6379/0"
    # the python-docx renderer until the Node library is ported (FIX_LOG D10)
    EXPORT_RENDERER       = "python-interim"
    PUBLIC_BASE_URL       = "https://${var.domain}"
    JWKS_URL              = module.identity_platform.jwks_url
    JWT_ISSUER            = module.identity_platform.issuer
    JWT_AUDIENCE          = module.identity_platform.audience
    LLM_ACCESS            = var.llm_access
    VERTEX_REGION         = var.region
    TASKS_QUEUE           = module.tasks.queue
    WORKER_URL            = local.api_url
    TASKS_SERVICE_ACCOUNT = local.sa.tasks
    TASKS_AUDIENCE        = local.tasks_audience
    RUN_JOB               = "${local.name}-run"
    BYOK_KMS_KEY          = google_kms_crypto_key.byok.id
  }
  db_secret = { CHECKPOINT_DATABASE_URL = "checkpoint-database-url" }
  providers_secret = {
    P_SEARCH_API_KEY    = "p-search-api-key"
    P_SCRAPE_API_KEY    = "p-scrape-api-key"
    P_SIGNALS_API_KEY   = "p-signals-api-key"
    P_SIGNALS_API_TOKEN = "p-signals-api-token"
    P_PEOPLE_API_KEY    = "p-people-api-key"
    P_ORG_API_KEY       = "p-org-api-key"
  }
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
  kms_key_id                  = module.secret_manager.kms_key_id
  iam_users                   = { api = local.sa.api, job = local.sa.job }
  backup_retention_days       = var.environment == "prod" ? 30 : 7
}

module "gcs" {
  source             = "../modules/gcs"
  project_id         = var.project_id
  region             = var.region
  name               = local.name
  kms_key_id         = module.secret_manager.kms_key_id
  object_users       = { api = local.sa.api, job = local.sa.job }
  raw_retention_days = var.raw_retention_days
}

module "cache" {
  source     = "../modules/cache"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  network_id = module.network.network_id
  depends_on = [module.network]
}

module "tasks" {
  source     = "../modules/tasks"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  depends_on = [module.project]
}

module "registry" {
  source     = "../modules/registry"
  project_id = var.project_id
  region     = var.region
  name       = local.name
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
  services = {
    api = {
      image    = var.images.api
      sa_email = local.sa.api
      port     = 8080
      # internal: the web server (direct VPC egress), Cloud Tasks and the load balancer only
      ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"
      env = merge(local.common_env, {
        SERVICE_ROLE           = "api"
        DB_IAM_USER            = module.cloud_sql.iam_users["api"]
        STAFF_JWT_ISSUER       = "https://accounts.google.com"
        STAFF_JWT_AUDIENCE     = var.staff.oauth_client_id
        STAFF_JWKS_URL         = "https://www.googleapis.com/oauth2/v3/certs"
        STAFF_WORKSPACE_DOMAIN = var.staff.workspace_domain
        STAFF_OPERATOR_EMAILS  = var.staff.operator_emails
        STAFF_SUPPORT_EMAILS   = var.staff.support_emails
      })
      secret_env    = local.db_secret
      min_instances = var.min_instances.api
      max_instances = 20
      cpu           = "1"
      memory        = "1Gi"
      timeout       = "300s"
      probe_path    = "/readyz"
      audiences     = [local.tasks_audience]
    }
    web = {
      image    = var.images.web
      sa_email = local.sa.web
      port     = 3000
      ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
      env = {
        API_ORIGIN                   = local.api_url
        STAFF_WORKSPACE_DOMAIN       = var.staff.workspace_domain
        OPERATOR_GOOGLE_CLIENT_ID    = var.staff.oauth_client_id
        OPERATOR_GOOGLE_REDIRECT_URI = "https://${var.domain}/api/operator/callback"
      }
      secret_env    = { OPERATOR_GOOGLE_CLIENT_SECRET = "operator-google-client-secret" }
      min_instances = var.min_instances.web
      max_instances = 20
      cpu           = "1"
      memory        = "512Mi"
      timeout       = "60s"
      probe_path    = "/login"
      audiences     = []
    }
  }
  # the api authenticates every business request itself; ingress keeps it off the internet
  public_invoker = ["api", "web"]
  invokers       = { api = { tasks = local.sa.tasks } }
  depends_on     = [module.secret_manager]
}

module "cloud_run_jobs" {
  source     = "../modules/cloud_run_jobs"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  network_id = module.network.network_id
  subnet_id  = module.network.subnet_id
  jobs = {
    run = {
      image       = var.images.worker
      sa_email    = local.sa.job
      command     = []
      args        = [] # --tenant-id and --run-id are set per execution by the api
      env         = merge(local.common_env, { SERVICE_ROLE = "job", DB_IAM_USER = module.cloud_sql.iam_users["job"] })
      secret_env  = merge(local.db_secret, local.providers_secret)
      timeout     = "18000s" # 5 h: above the 60 x 240 s slice ceiling
      max_retries = 1
      cpu         = "1"
      memory      = "2Gi"
    }
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
      image       = var.images.api
      sa_email    = local.sa.migrate
      command     = ["alembic", "upgrade", "head"]
      args        = []
      env         = { APP_ENV = local.app_env, SERVICE_ROLE = "migrate" }
      secret_env  = { MIGRATOR_DATABASE_URL = "migrator-database-url", APP_RW_PASSWORD = "app-rw-password" }
      timeout     = "1800s"
      max_retries = 0
      cpu         = "1"
      memory      = "1Gi"
    }
  }
  runners = {
    run              = { api = local.sa.api }
    spend-invariants = { scheduler = local.sa.scheduler }
    migrate          = { deployer = local.sa.deployer }
  }
  depends_on = [module.secret_manager]
}

module "scheduler" {
  source     = "../modules/scheduler"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  sa_email   = local.sa.scheduler
  time_zone  = "Etc/UTC"
  jobs = {
    spend-invariants = { schedule = "17 3 * * *", job_name = module.cloud_run_jobs.names["spend-invariants"] }
  }
}

module "edge" {
  source                = "../modules/edge"
  project_id            = var.project_id
  region                = var.region
  name                  = local.name
  domain                = var.domain
  web_service_name      = module.cloud_run.names["web"]
  rate_limit_per_minute = 600
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
output "api_url" {
  value = local.api_url
}
