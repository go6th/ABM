# Private IP only, IAM authentication, backups with point-in-time recovery, deletion protection.
mock_provider "google" {}
mock_provider "google-beta" {
  mock_resource "google_project_service_identity" {
    defaults = { email = "service-1@gcp-sa-cloud-sql.iam.gserviceaccount.com" }
  }
}

variables {
  project_id                  = "synthetic"
  region                      = "us-central1"
  name                        = "ai-prod"
  network_id                  = "projects/synthetic/global/networks/vpc"
  private_services_connection = "psa"
  tier                        = "db-custom-2-7680"
  availability_type           = "REGIONAL"
  max_connections             = 400
  kms_key_id                  = "projects/synthetic/locations/us-central1/keyRings/k/cryptoKeys/data"
  iam_users                   = { api = "ai-prod-api@synthetic.iam.gserviceaccount.com" }
  backup_retention_days       = 30
}

run "database_is_private_and_recoverable" {
  command = plan
  assert {
    condition     = google_sql_database_instance.main.settings[0].ip_configuration[0].ipv4_enabled == false
    error_message = "no public IP"
  }
  assert {
    condition     = google_sql_database_instance.main.settings[0].backup_configuration[0].point_in_time_recovery_enabled
    error_message = "point-in-time recovery"
  }
  assert {
    condition     = google_sql_database_instance.main.deletion_protection && google_sql_database_instance.main.settings[0].deletion_protection_enabled
    error_message = "deletion protection in Terraform and in the API"
  }
  assert {
    condition     = contains([for f in google_sql_database_instance.main.settings[0].database_flags : "${f.name}=${f.value}"], "cloudsql.iam_authentication=on")
    error_message = "IAM database authentication"
  }
  assert {
    condition     = google_sql_database_instance.main.settings[0].availability_type == "REGIONAL" && google_sql_database_instance.main.settings[0].edition == "ENTERPRISE"
    error_message = "HA from the variable; Enterprise edition for db-custom tiers"
  }
  assert {
    condition     = google_sql_user.iam["api"].name == "ai-prod-api@synthetic.iam" && google_sql_user.iam["api"].type == "CLOUD_IAM_SERVICE_ACCOUNT"
    error_message = "IAM user name is the account email without .gserviceaccount.com"
  }
}

run "availability_is_validated" {
  command = plan
  variables {
    availability_type = "MULTI"
  }
  expect_failures = [var.availability_type]
}
