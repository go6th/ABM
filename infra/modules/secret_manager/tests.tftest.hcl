# Each secret is readable only by the accounts listed for it.
mock_provider "google" {}
mock_provider "google-beta" {
  mock_resource "google_project_service_identity" {
    defaults = { email = "service-1@gcp-sa-secretmanager.iam.gserviceaccount.com" }
  }
}

variables {
  project_id = "synthetic"
  region     = "us-central1"
  name       = "ai-staging"
  secrets = {
    "anthropic-api-key"     = ["api", "job"]
    "migrator-database-url" = ["migrate"]
  }
  accounts = {
    api     = "api@x.iam.gserviceaccount.com"
    job     = "job@x.iam.gserviceaccount.com"
    migrate = "migrate@x.iam.gserviceaccount.com"
  }
}

run "least_privilege_secret_access" {
  command = plan
  assert {
    condition     = length(google_secret_manager_secret_iam_member.access) == 3
    error_message = "exactly one accessor binding per (secret, reader)"
  }
  assert {
    condition     = google_secret_manager_secret_iam_member.access["migrator-database-url:migrate"].member == "serviceAccount:migrate@x.iam.gserviceaccount.com"
    error_message = "accessor role only"
  }
}
