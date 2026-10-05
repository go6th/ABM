# Environment stack: production. Generated from infra/envs/main.tf.tmpl by scripts/gen_envs.sh so the
# three environments stay identical except their values (terraform.tfvars, not committed).
# Nothing here is applied by CI or by the build: the owner runs plan and apply (docs/runbooks).

terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # State in a bucket the owner creates first (docs/runbooks/bootstrap.md); bucket and prefix are
  # passed with -backend-config at init. Validation runs with -backend=false.
  backend "gcs" {}
}

variable "project_id" {
  type        = string
  description = "The production project (one project per environment, D-20)."
}
variable "region" {
  type        = string
  default     = "asia-south1"
  description = "D-20 default region. VERIFY(V-REGION-1): service and model availability."
}
variable "github_repository" {
  type = string
}
variable "image" {
  type        = string
  description = "Service image by digest, from CI."
}
variable "db_tier" {
  type        = string
  description = "Cloud SQL tier, chosen by the owner (no size is assumed)."
}
variable "db_availability" {
  type    = string
  default = "REGIONAL"
}
variable "llm_access" {
  type        = string
  default     = "vertex_ai"
  description = "D-19: vertex_ai or anthropic_api."
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  name     = "ai-production"
  audience = "ai-production-worker"
  # VERIFY(V-RUN-1): Cloud Run's deterministic URL form https://SERVICE-PROJECT_NUMBER.REGION.run.app
  worker_url = "https://${local.name}-worker-${data.google_project.this.number}.${var.region}.run.app"
  secrets = ["p-search-api-key", "p-scrape-api-key", "p-signals-api-key", "p-signals-api-token",
  "p-people-api-key", "p-org-api-key", "anthropic-api-key", "checkpoint-database-url"]
}

module "project" {
  source     = "../../modules/project"
  project_id = var.project_id
}

module "identity" {
  source            = "../../modules/identity"
  project_id        = var.project_id
  name              = local.name
  github_repository = var.github_repository
  depends_on        = [module.project]
}

module "network" {
  source     = "../../modules/network"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  depends_on = [module.project]
}

module "secrets" {
  source           = "../../modules/secrets"
  project_id       = var.project_id
  region           = var.region
  name             = local.name
  secret_ids       = local.secrets
  runtime_sa_email = module.identity.runtime_email
}

module "database" {
  source                      = "../../modules/database"
  project_id                  = var.project_id
  region                      = var.region
  name                        = local.name
  network_id                  = module.network.network_id
  private_services_connection = module.network.private_services_connection
  tier                        = var.db_tier
  availability_type           = var.db_availability
  kms_key_id                  = module.secrets.kms_key_id
  runtime_sa_email            = module.identity.runtime_email
}

module "storage" {
  source           = "../../modules/storage"
  project_id       = var.project_id
  region           = var.region
  name             = local.name
  kms_key_id       = module.secrets.kms_key_id
  runtime_sa_email = module.identity.runtime_email
}

module "tasks" {
  source     = "../../modules/tasks"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  depends_on = [module.project]
}

module "registry" {
  source     = "../../modules/registry"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  depends_on = [module.project]
}

module "cache" {
  source     = "../../modules/cache"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  network_id = module.network.network_id
  depends_on = [module.network]
}

module "runtime" {
  source           = "../../modules/runtime"
  project_id       = var.project_id
  region           = var.region
  name             = local.name
  app_env          = "production"
  image            = var.image
  runtime_sa_email = module.identity.runtime_email
  tasks_sa_email   = module.identity.tasks_email
  tasks_audience   = local.audience
  network_id       = module.network.network_id
  subnet_id        = module.network.subnet_id
  env = {
    GCP_PROJECT           = var.project_id
    GCP_REGION            = var.region
    TASKS_QUEUE           = module.tasks.queue
    WORKER_URL            = local.worker_url
    TASKS_SERVICE_ACCOUNT = module.identity.tasks_email
    TASKS_AUDIENCE        = local.audience
    PAYLOAD_BUCKET        = module.storage.bucket
    CLOUD_SQL_INSTANCE    = module.database.connection_name
    DB_IAM_USER           = module.database.iam_user
    DB_NAME               = module.database.database
    LLM_ACCESS            = var.llm_access
    REDIS_HOST            = module.cache.host
  }
  secret_env = {
    CHECKPOINT_DATABASE_URL = "checkpoint-database-url"
    P_SEARCH_API_KEY        = "p-search-api-key"
    P_SCRAPE_API_KEY        = "p-scrape-api-key"
    P_SIGNALS_API_KEY       = "p-signals-api-key"
    P_SIGNALS_API_TOKEN     = "p-signals-api-token"
    P_PEOPLE_API_KEY        = "p-people-api-key"
    P_ORG_API_KEY           = "p-org-api-key"
  }
  depends_on = [module.secrets]
}

output "api_url" {
  value = module.runtime.api_url
}
output "worker_url" {
  value = module.runtime.worker_url
}
output "nat_ip" {
  value = module.network.nat_ip
}
output "wif_provider" {
  value = module.identity.wif_provider
}
output "deployer_email" {
  value = module.identity.deployer_email
}
output "registry" {
  value = module.registry.repository
}
