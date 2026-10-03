# Memorystore for Redis: the connector rate limiter's shared store, so every worker instance backs
# off together after a 429 (RedisRateStore). Private service access only.

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
variable "memory_size_gb" {
  type    = number
  default = 1
}

resource "google_redis_instance" "rate" {
  project                 = var.project_id
  name                    = "${var.name}-rate"
  region                  = var.region
  tier                    = "BASIC"
  memory_size_gb          = var.memory_size_gb
  authorized_network      = var.network_id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  transit_encryption_mode = "SERVER_AUTHENTICATION"
}

output "host" {
  value = google_redis_instance.rate.host
}
