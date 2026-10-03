# Private network: one VPC and subnet for Cloud Run direct VPC egress, private services access for
# Cloud SQL (no public IP), and Cloud NAT so outbound provider calls leave from a fixed egress path
# (decision D-20: egress through NAT; providers can allow-list the NAT addresses).

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type        = string
  description = "Name prefix, e.g. ai-staging."
}
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/24"
}

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "run" {
  project                  = var.project_id
  name                     = "${var.name}-run"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_global_address" "private_services" {
  project       = var.project_id
  name          = "${var.name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

resource "google_compute_router" "router" {
  project = var.project_id
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_address" "nat" {
  project = var.project_id
  name    = "${var.name}-nat-ip"
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${var.name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.nat.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

output "network_id" {
  value = google_compute_network.vpc.id
}
output "subnet_id" {
  value = google_compute_subnetwork.run.id
}
output "nat_ip" {
  value       = google_compute_address.nat.address
  description = "The fixed egress address providers may allow-list."
}
output "private_services_connection" {
  value = google_service_networking_connection.private_services.id
}
