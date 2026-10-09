# The deployer can be impersonated only from the protected GitHub environment, repository and ref.
mock_provider "google" {}

variables {
  project_id         = "synthetic"
  name               = "ai-prod"
  github_repository  = "owner/repo"
  deploy_ref         = "refs/heads/main"
  github_environment = "production"
  vertex_ai          = false
}

run "wif_is_scoped" {
  command = plan
  assert {
    condition = google_iam_workload_identity_pool_provider.github.attribute_condition == join(" && ", [
      "assertion.repository == \"owner/repo\"",
      "assertion.ref == \"refs/heads/main\"",
      "assertion.environment == \"production\"",
    ])
    error_message = "WIF attribute condition must pin repository, ref and environment"
  }
  assert {
    condition     = !contains([for b in google_project_iam_member.role : b.role], "roles/run.admin")
    error_message = "no workload or deployer holds project-wide run.admin"
  }
  assert {
    condition     = !contains([for b in google_project_iam_member.role : b.role], "roles/owner") && !contains([for b in google_project_iam_member.role : b.role], "roles/editor")
    error_message = "no basic roles"
  }
  assert {
    condition     = !contains([for k, b in google_project_iam_member.role : b.role if startswith(k, "web:")], "roles/cloudsql.client")
    error_message = "web has no database access"
  }
}
