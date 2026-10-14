# Environment: prod. Values come from terraform.tfvars (not committed; see the .example file).
# State lives in a GCS bucket the owner creates first (docs/runbooks/bootstrap.md), passed at init
# with -backend-config="bucket=..." -backend-config="prefix=prod". Validation and the offline plan
# test run with -backend=false.

terraform {
  required_version = ">= 1.9"
  backend "gcs" {}
}

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "billing_account" {
  type = string
}
variable "domain" {
  type = string
}
variable "github_repository" {
  type = string
}
variable "images" {
  type = object({ api = string, worker = string, web = string })
}
variable "clamav_image" {
  type = string
}
variable "db_tier" {
  type = string
}
variable "db_availability" {
  type        = string
  description = "REGIONAL is high availability (a standby in a second zone); ZONAL is one zone."
  default     = "REGIONAL"
}
variable "db_max_connections" {
  type    = number
  default = 400
}
variable "redis_tier" {
  type    = string
  default = "STANDARD_HA"
}
variable "scaling" {
  type = object({
    api    = object({ min = number, max = number })
    web    = object({ min = number, max = number })
    worker = object({ min = number, max = number })
  })
  default = { api = { min = 1, max = 10 }, web = { min = 1, max = 10 }, worker = { min = 1, max = 8 } }
}
variable "llm_access" {
  type = string
}
variable "moonshot_enabled" {
  type    = bool
  default = false
}
variable "moonshot_api" {
  type = object({
    base_url = string
    dialect  = string
    model    = string
  })
  default = { base_url = "https://api.moonshot.ai/v1", dialect = "moonshot", model = "" }
}
variable "maildrive" {
  type = object({
    google_client_id    = string
    microsoft_client_id = string
  })
  default = { google_client_id = "", microsoft_client_id = "" }
}
variable "archive_lock" {
  type    = bool
  default = false
}
variable "workload_alerts_enabled" {
  type    = bool
  default = false
}
variable "monthly_budget_usd" {
  type = number
}
variable "alert_channels" {
  type = list(string)
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
    external_emails  = optional(string, "")
  })
}

module "stack" {
  source                  = "../../stack"
  environment             = "prod"
  project_id              = var.project_id
  region                  = var.region
  billing_account         = var.billing_account
  domain                  = var.domain
  github_repository       = var.github_repository
  deploy_ref              = "refs/heads/main"
  images                  = var.images
  clamav_image            = var.clamav_image
  db_tier                 = var.db_tier
  db_availability         = var.db_availability
  db_max_connections      = var.db_max_connections
  redis_tier              = var.redis_tier
  scaling                 = var.scaling
  llm_access              = var.llm_access
  moonshot_enabled        = var.moonshot_enabled
  moonshot_api            = var.moonshot_api
  maildrive               = var.maildrive
  archive_lock            = var.archive_lock
  workload_alerts_enabled = var.workload_alerts_enabled
  monthly_budget_usd      = var.monthly_budget_usd
  alert_channels          = var.alert_channels
  raw_retention_days      = var.raw_retention_days
  staff                   = var.staff
}

output "web_ip" {
  value = module.stack.web_ip
}
output "wif_provider" {
  value = module.stack.wif_provider
}
output "deployer_email" {
  value = module.stack.deployer_email
}
output "registry" {
  value = module.stack.registry
}
output "services" {
  value = module.stack.services
}
output "jobs" {
  value = module.stack.jobs
}
output "api_url" {
  value = module.stack.api_url
}
output "worker_url" {
  value = module.stack.worker_url
}
output "nat_ip" {
  value = module.stack.nat_ip
}
output "queues" {
  value = module.stack.queues
}
output "buckets" {
  value = module.stack.buckets
}
output "sql_connection_name" {
  value = module.stack.sql_connection_name
}
output "service_accounts" {
  value = module.stack.service_accounts
}
