# staging environment. Cloud and region come from terraform.tfvars (or TF_VAR_* overrides).
# Remote state: add a backend block here once the cloud is chosen (see infra/README.md).

terraform {
  required_version = ">= 1.6"
}

variable "cloud_provider" {
  type = string
}

variable "region" {
  type = string
}

variable "worker_min_replicas" {
  type = number
}

variable "worker_max_replicas" {
  type = number
}

variable "deletion_protection" {
  type = bool
}

variable "model_mode" {
  type = string
}

variable "broker_mode" {
  type = string
}

module "environment" {
  source              = "../../modules/environment"
  environment         = "staging"
  cloud_provider      = var.cloud_provider
  region              = var.region
  worker_min_replicas = var.worker_min_replicas
  worker_max_replicas = var.worker_max_replicas
  deletion_protection = var.deletion_protection
  model_mode          = var.model_mode
  broker_mode         = var.broker_mode
}

output "manifest" {
  value = module.environment.manifest
}
