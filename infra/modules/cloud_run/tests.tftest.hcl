# The worker is private: internal ingress, no allUsers, run.invoker for the tasks identity only.
mock_provider "google" {}

variables {
  project_id = "synthetic"
  region     = "us-central1"
  name       = "ai-staging"
  network_id = "projects/synthetic/global/networks/vpc"
  subnet_id  = "projects/synthetic/regions/us-central1/subnetworks/run"
  services = {
    api = {
      image               = "r/api@sha256:0", sa_email = "api@x.iam.gserviceaccount.com", port = 8080
      ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY", env = {}, secret_env = {}
      min_instances       = 0, max_instances = 3, concurrency = 40, cpu = "1", memory = "1Gi"
      cpu_idle            = true, timeout = "300s", startup_probe_path = "/readyz"
      liveness_probe_path = "/healthz", audiences = []
    }
    worker = {
      image               = "r/worker@sha256:0", sa_email = "worker@x.iam.gserviceaccount.com", port = 8080
      ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY", env = {}, secret_env = { A = "a" }
      min_instances       = 0, max_instances = 2, concurrency = 4, cpu = "4", memory = "8Gi"
      cpu_idle            = false, gen2 = true, timeout = "3600s", startup_probe_path = "/readyz"
      liveness_probe_path = "/healthz", audiences = ["ai-staging-worker-tasks"]
      gcs_mounts          = { "/mnt/blobs" = "ai-staging-data" }
      secret_files        = { "/etc/redis-ca" = { secret = "redis-server-ca", file = "ca.pem" } }
      sidecars            = { clamd = { image = "r/clamav@sha256:0", cpu = "1", memory = "4Gi", probe_port = 3310 } }
      gpu                 = { accelerator = "nvidia-l4" }
    }
  }
  public_invoker = ["api"]
  invokers       = { worker = { tasks = "tasks@x.iam.gserviceaccount.com" } }
}

run "worker_is_private" {
  command = plan
  assert {
    condition     = !contains(keys(google_cloud_run_v2_service_iam_member.public), "worker")
    error_message = "the worker never gets allUsers"
  }
  assert {
    condition     = keys(google_cloud_run_v2_service_iam_member.invoker) == ["worker:tasks"] && google_cloud_run_v2_service_iam_member.invoker["worker:tasks"].member == "serviceAccount:tasks@x.iam.gserviceaccount.com"
    error_message = "only the tasks identity invokes the worker"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    error_message = "worker ingress is internal"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].custom_audiences == tolist(["ai-staging-worker-tasks"])
    error_message = "the worker accepts the fixed tasks audience"
  }
}

run "worker_shape" {
  command = plan
  assert {
    condition     = length(google_cloud_run_v2_service.svc["worker"].template[0].containers) == 2
    error_message = "worker plus its clamd sidecar"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].template[0].containers[0].resources[0].cpu_idle == false
    error_message = "the worker keeps CPU between requests (background sweeper)"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].template[0].execution_environment == "EXECUTION_ENVIRONMENT_GEN2"
    error_message = "gen2 for the Cloud Storage mount"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].template[0].node_selector[0].accelerator == "nvidia-l4" && google_cloud_run_v2_service.svc["worker"].template[0].containers[0].resources[0].limits["nvidia.com/gpu"] == "1" && google_cloud_run_v2_service.svc["worker"].template[0].gpu_zonal_redundancy_disabled == true
    error_message = "the GPU goes on the worker container alone, without zonal redundancy"
  }
  assert {
    condition     = !contains(keys(google_cloud_run_v2_service.svc["worker"].template[0].containers[1].resources[0].limits), "nvidia.com/gpu") && length(google_cloud_run_v2_service.svc["api"].template[0].node_selector) == 0
    error_message = "no GPU on the sidecar or on a service that did not ask for one"
  }
  assert {
    condition     = alltrue([for v in google_cloud_run_v2_service.svc["worker"].template[0].volumes : v.gcs[0].read_only if length(v.gcs) > 0])
    error_message = "the bucket is mounted read only"
  }
  assert {
    condition     = google_cloud_run_v2_service.svc["worker"].template[0].max_instance_request_concurrency == 4
    error_message = "worker concurrency"
  }
}
