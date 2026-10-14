# Memorystore for Redis on the private network: the shared rate limiters (sign-in and providers,
# so every instance backs off together after a 429) and the research cache index. Private service
# access only, in-transit encryption (TLS on port 6378 with the instance's own CA, which the stack
# mounts into the services as a file; VERIFY(V-REDIS-1)). AUTH is off on purpose: the AUTH string
# would sit in Terraform state, and the instance is reachable only from this project's VPC, whose
# only clients are the Cloud Run services (docs/deploy/ARCHITECTURE.md).

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
variable "private_services_connection" {
  type        = string
  description = "Dependency handle: private service access needs the peering first."
}
variable "tier" {
  type        = string
  description = "BASIC (staging) or STANDARD_HA (production: a replica in another zone)."
  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.tier)
    error_message = "tier must be BASIC or STANDARD_HA."
  }
}
variable "memory_size_gb" {
  type    = number
  default = 1
}
variable "labels" {
  type    = map(string)
  default = {}
}

resource "google_redis_instance" "rate" {
  # checkov:skip=CKV_GCP_95:the AUTH string would be stored in Terraform state; the instance is reachable only from this project's VPC (see the header)
  project                 = var.project_id
  name                    = "${var.name}-rate"
  region                  = var.region
  tier                    = var.tier
  memory_size_gb          = var.memory_size_gb
  redis_version           = "REDIS_7_2"
  authorized_network      = var.network_id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  labels                  = var.labels
  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 4
        minutes = 0
      }
    }
  }
  depends_on = [var.private_services_connection]
}

output "host" {
  value = google_redis_instance.rate.host
}
output "port" {
  value       = google_redis_instance.rate.port
  description = "6378 with in-transit encryption."
}
output "server_ca_pem" {
  value       = join("\n", [for c in google_redis_instance.rate.server_ca_certs : c.cert])
  description = "Public CA certificate(s) of the instance's TLS endpoint (not a secret)."
}
