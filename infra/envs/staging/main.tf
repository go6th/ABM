# Environment: staging. Values come from terraform.tfvars (not committed; see the .example file).
# State lives in a GCS bucket the owner creates first (docs/runbooks/bootstrap.md), passed at init
# with -backend-config="bucket=..." -backend-config="prefix=staging". Validation and the offline plan
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
variable "db_tier" {
  type = string
}
variable "llm_access" {
  type = string
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
  })
}

module "stack" {
  source             = "../../stack"
  environment        = "staging"
  project_id         = var.project_id
  region             = var.region
  billing_account    = var.billing_account
  domain             = var.domain
  github_repository  = var.github_repository
  deploy_ref         = "refs/heads/main"
  images             = var.images
  db_tier            = var.db_tier
  db_availability    = "ZONAL"
  llm_access         = var.llm_access
  monthly_budget_usd = var.monthly_budget_usd
  alert_channels     = var.alert_channels
  raw_retention_days = var.raw_retention_days
  staff              = var.staff
  min_instances      = { api = 0, web = 0 }
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
