# Worker ticks carry the tasks identity's OIDC token for the worker's audience; Phase 5 ticks start
# paused.
mock_provider "google" {}

variables {
  project_id    = "synthetic"
  region        = "us-central1"
  name          = "ai-staging"
  sa_email      = "scheduler@x.iam.gserviceaccount.com"
  time_zone     = "Etc/UTC"
  jobs          = {}
  worker_url    = "https://ai-staging-worker-1.us-central1.run.app"
  tick_sa_email = "tasks@x.iam.gserviceaccount.com"
  tick_audience = "ai-staging-worker-tasks"
  http_ticks = {
    isolation-check = { schedule = "0 2 * * *", path = "/tasks/isolation-check", deadline = "1800s", paused = false }
    retention       = { schedule = "45 3 * * *", path = "/tasks/retention", deadline = "1800s", paused = true }
  }
}

run "ticks" {
  command = plan
  assert {
    condition     = google_cloud_scheduler_job.tick["isolation-check"].http_target[0].uri == "https://ai-staging-worker-1.us-central1.run.app/tasks/isolation-check"
    error_message = "tick calls the worker route"
  }
  assert {
    condition     = google_cloud_scheduler_job.tick["isolation-check"].http_target[0].oidc_token[0].service_account_email == "tasks@x.iam.gserviceaccount.com" && google_cloud_scheduler_job.tick["isolation-check"].http_target[0].oidc_token[0].audience == "ai-staging-worker-tasks"
    error_message = "the identity and audience the worker checks"
  }
  assert {
    condition     = google_cloud_scheduler_job.tick["retention"].paused && !google_cloud_scheduler_job.tick["isolation-check"].paused
    error_message = "Phase 5 ticks start paused"
  }
}
