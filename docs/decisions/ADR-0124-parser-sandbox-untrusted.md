# ADR-0124: the parser sandbox treats the parser as untrusted

- Status: Accepted (short term built); the separate parse job is proposed, not built
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 3)

## Context

`documents/sandbox.py` runs native parsers (lxml/libxml2, pypdf, openpyxl, Pillow) on uploaded
files in a child process (`documents/sandbox_child.py`). Already in place before this change:
an empty environment, Python isolated mode, `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE = 0`,
`RLIMIT_CORE = 0`, a parent wall-clock timeout and text cap, rejection of a non-zero exit, a
**seccomp filter** (raw classic BPF installed through `ctypes`, no extra dependency: socket,
connect, bind, accept, execve, fork, vfork, ptrace, io_uring and non-thread clone refused) and an
audit hook refusing network, processes, native loads and writes. The brief assumed seccomp was
missing; code is truth: it exists and is fail-closed on Linux.

Gaps: the child opened the document **by path** and could read any file its user can read. On
the worker the whole payload bucket is mounted read-only at `/mnt/blobs` (every tenant's raw
uploads), and `/proc/<parent>/environ` (same uid) holds the worker's secrets. A parser bug
steered by a hostile file could put those bytes into the attacker's own document text. There was
no open-file limit, no network namespace and no record of what was applied.

## Decision (built)

| Protection | Now | Cloud Run gen2 |
|---|---|---|
| Document as a read-only fd 0 opened by the parent; the child never gets the path | yes | works |
| Python-level reads and directory listings confined to the code and config (`sys.prefix`, `sys.path`, `CONFIG_DIR`, `/usr`) via the audit hook; `/mnt/blobs`, `/proc`, `/etc`, `/tmp` refused | yes (`mimetypes` is initialised before the lock, openpyxl needs it) | works (pure Python) |
| `RLIMIT_NOFILE = 64` beside AS, CPU, FSIZE 0, CORE 0 | yes | works |
| Hard wall clock | parent timeout (existing) | works |
| RSS limit | `RLIMIT_AS` (virtual, stricter); `RLIMIT_RSS` is a no-op on Linux | works |
| Network namespace: `unshare(CLONE_NEWNET)`, else with `CLONE_NEWUSER` | attempted, recorded `netns=on|unavailable` | expected **unavailable** (no CAP_SYS_ADMIN, no user namespaces in the gen2 sandbox); the seccomp socket ban is the network control there |
| Drop to `nobody` when running as root | yes (`uid=dropped|not_root`) | the images run as uid 10001, so `not_root`: the uid drop never applies in production |
| seccomp (existing) | on, fail-closed on Linux | VERIFY(V-SANDBOX-1): gen2 accepts the filter (the existing tests run it locally) |
| Non-zero exit rejected, even after units were printed | existing, now tested | works |
| Record of what was applied | `{"sandbox": {...}}` from the child; the parent fills `applied` and logs `documents.sandbox_applied` once per distinct set (`degraded=true` if seccomp is off) | works |

No new dependency was added.

**Residual risk.** Native code that a hostile document takes over is held by the kernel filter (no
network, no process, no ptrace) but can still `open()` any file uid 10001 can read, including
other tenants' documents on the bucket mount and `/proc/<parent>/environ`, and print it as text.
Closing that needs the filesystem confined in the kernel (Landlock, or a mount namespace, neither
available on Cloud Run gen2 to an unprivileged process), or the separate job below.

## Decision (proposed, not built): parsing as its own Cloud Run job

A Cloud Run job per document part (or a small pool), its own service account `parse@`, no bucket
mount: it reads exactly one quarantined object (the worker hands it a V4 signed URL or the object
name in a per-object prefix the job may read), writes units to a per-document results prefix,
and acks on the documents queue. It holds no database credential, no provider key, no Secret
Manager access; egress is off except Google APIs through Private Google Access.

```hcl
resource "google_service_account" "parse" { account_id = "${local.name}-parse" }

resource "google_storage_bucket" "quarantine" {
  name                        = "${local.name}-quarantine"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  lifecycle_rule {
    condition { age = 1 }
    action { type = "Delete" }
  }
}

# read the quarantined inputs, write results under results/ only
resource "google_storage_bucket_iam_member" "parse_read" {
  bucket = google_storage_bucket.quarantine.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.parse.email}"
}
resource "google_storage_bucket_iam_member" "parse_write_results" {
  bucket = google_storage_bucket.quarantine.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.parse.email}"
  condition {
    title      = "results-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.quarantine.name}/objects/results/\")"
  }
}
resource "google_cloud_tasks_queue_iam_member" "parse_ack" {
  name   = google_cloud_tasks_queue.documents.name
  role   = "roles/cloudtasks.enqueuer"
  member = "serviceAccount:${google_service_account.parse.email}"
}

resource "google_cloud_run_v2_job" "parse" {
  name     = "${local.name}-parse"
  location = var.region
  template {
    template {
      service_account = google_service_account.parse.email
      max_retries     = 0
      timeout         = "300s"
      vpc_access {
        network_interfaces {
          network    = var.network_id
          subnetwork = var.subnet_id
        }
        egress = "ALL_TRAFFIC" # with a firewall that allows only Private Google Access
      }
      containers {
        image   = var.images.parser # the worker image minus app code and secrets
        command = ["python", "-I", "-m", "documents.parse_job"]
        resources { limits = { cpu = "1", memory = "2Gi" } }
      }
    }
  }
}
```

## Consequences

A Python-level parser that tries to read another file now fails its parse. The local profile
records `netns=on` (user namespaces allowed on the developer machine); staging will record
`netns=unavailable` and `uid=not_root`, which is expected. Check after a deploy: Logs Explorer,
`jsonPayload.event="documents.sandbox_applied"` on the worker shows `seccomp=on` and
`degraded=false`.
