# Three queues; a missing one is refused.
mock_provider "google" {}

variables {
  project_id = "synthetic"
  region     = "us-central1"
  name       = "ai-staging"
  queues = {
    runs      = { max_dispatches_per_second = 5, max_concurrent_dispatches = 4, max_attempts = 5, min_backoff = "10s", max_backoff = "300s", max_doublings = 4 }
    documents = { max_dispatches_per_second = 2, max_concurrent_dispatches = 2, max_attempts = 10, min_backoff = "30s", max_backoff = "600s", max_doublings = 4 }
    jobs      = { max_dispatches_per_second = 5, max_concurrent_dispatches = 2, max_attempts = 5, min_backoff = "10s", max_backoff = "300s", max_doublings = 4 }
  }
}

run "queues" {
  command = plan
  assert {
    condition     = output.queue == "ai-staging-runs" && length(output.queues) == 3
    error_message = "runs, documents and jobs"
  }
  assert {
    condition     = google_cloud_tasks_queue.q["documents"].retry_config[0].max_attempts == 10
    error_message = "documents retry through busy answers"
  }
}

run "a_missing_queue_is_refused" {
  command = plan
  variables {
    queues = {
      runs = { max_dispatches_per_second = 5, max_concurrent_dispatches = 4, max_attempts = 5, min_backoff = "10s", max_backoff = "300s", max_doublings = 4 }
    }
  }
  expect_failures = [var.queues]
}
