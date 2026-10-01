# One Account Intelligence environment (dev, staging or production).
#
# Cloud-neutral on purpose: the cloud and region are open decisions (roadmap s16, decide by
# week 2). This module fixes the environment contract every cloud implementation must meet:
# names, storage prefixes, the secret names the app reads, worker scaling and protection
# settings. When the cloud is chosen, add the provider resources in a sibling module that
# consumes `local.manifest`; nothing in the app changes.

terraform {
  required_version = ">= 1.6"
}

locals {
  name = "${var.name_prefix}-${var.environment}"

  # Every secret the app reads, injected as environment variables from the secrets manager
  # (roadmap s11.4). Values never live in this repository or in Terraform state.
  secret_names = [
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "PARALLEL_API_KEY",
    "FIRECRAWL_API_KEY",
    "PREDICTLEADS_API_KEY",
    "PREDICTLEADS_API_TOKEN",
    "BITSCALE_API_KEY",
    "SUMBLE_API_KEY",
  ]

  manifest = {
    environment        = var.environment
    cloud_provider     = var.cloud_provider
    region             = var.region
    name               = local.name
    temporal_namespace = "${local.name}"
    task_queue         = "account-intelligence"
    raw_bucket         = "${local.name}-raw"
    exports_bucket     = "${local.name}-exports"
    secret_path        = "${var.name_prefix}/${var.environment}"
    secret_names       = local.secret_names
    worker = {
      min_replicas = var.worker_min_replicas
      max_replicas = var.worker_max_replicas
    }
    deletion_protection = var.deletion_protection
    log_retention_days  = var.log_retention_days
    model_mode          = var.model_mode
    broker_mode         = var.broker_mode
  }
}

resource "terraform_data" "environment" {
  input = local.manifest

  lifecycle {
    precondition {
      condition     = var.environment != "production" || var.cloud_provider != "unset"
      error_message = "Production needs a chosen cloud_provider (open decision, roadmap s16)."
    }
    precondition {
      condition     = var.environment != "production" || var.deletion_protection
      error_message = "Production must keep deletion_protection on (roadmap s3 principle 5)."
    }
    precondition {
      condition     = var.environment == "production" || var.broker_mode == "recorded" || var.cloud_provider != "unset"
      error_message = "Live broker mode needs a real environment with a chosen cloud."
    }
  }
}
