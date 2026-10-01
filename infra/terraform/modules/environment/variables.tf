variable "environment" {
  type        = string
  description = "dev, staging or production"
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging or production."
  }
}

variable "cloud_provider" {
  type        = string
  description = "Chosen cloud. 'unset' until the Phase 0 cloud and region decision is made."
  validation {
    condition     = contains(["unset", "aws", "gcp", "azure"], var.cloud_provider)
    error_message = "cloud_provider must be unset, aws, gcp or azure."
  }
}

variable "region" {
  type        = string
  description = "Hosting region. Single region first, designed for a regional split (roadmap s16)."
  validation {
    condition     = length(trimspace(var.region)) > 0
    error_message = "region must not be empty; use 'unset' until decided."
  }
}

variable "name_prefix" {
  type    = string
  default = "acct-intel"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 21 lowercase letters, digits or hyphens."
  }
}

variable "worker_min_replicas" {
  type    = number
  default = 0
}

variable "worker_max_replicas" {
  type    = number
  default = 1
  validation {
    condition     = var.worker_max_replicas >= 1
    error_message = "worker_max_replicas must be at least 1."
  }
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "model_mode" {
  type    = string
  default = "fake"
  validation {
    condition     = contains(["fake", "live"], var.model_mode)
    error_message = "model_mode must be fake or live."
  }
}

variable "broker_mode" {
  type    = string
  default = "recorded"
  validation {
    condition     = contains(["recorded", "live"], var.broker_mode)
    error_message = "broker_mode must be recorded or live."
  }
}
