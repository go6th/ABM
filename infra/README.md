# `infra/`: Google Cloud as Terraform

One environment = one Google Cloud project, built from the same stack.

```
infra/
├── stack/main.tf          The whole environment composed from the modules (sizes, queues, ticks,
│                          secrets wiring, env vars of every service). stack/tests: offline plan tests.
├── envs/staging/main.tf   The staging root: calls ../../stack with staging values. terraform.tfvars
│                          (git-ignored) holds the owner inputs AND the image digests to deploy.
├── envs/prod/main.tf      The production root (prepared, not applied).
├── modules/               One module per cloud concern:
│   project, iam, network, secret_manager, cloud_sql, gcs, cache (Redis), tasks (Cloud Tasks
│   queues: runs, documents, jobs), registry (Artifact Registry), identity_platform, cloud_run
│   (api, worker, web; the worker carries the clamd scan sidecar), cloud_run_jobs (migrate,
│   spend-invariants), scheduler (the /tasks/* ticks), edge (HTTPS load balancer, Cloud Armor),
│   observability (log sinks, metrics, alerts, dashboards: the generated_*.tf.json files come
│   from scripts/gen_observability.py), budget.
└── .tflint.hcl
```

Live today: project `abmgenerator`, region `us-central1`, services `ai-staging-api`,
`ai-staging-worker`, `ai-staging-web`, job `ai-staging-migrate`, registry `ai-staging-images`.

How to change it:

```bash
cd infra/envs/staging
# record the image digests you want in terraform.tfvars first: Terraform sets the Cloud Run images
GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true /snap/bin/terraform plan -out=plan.tfplan
GOOGLE_BILLING_PROJECT=abmgenerator USER_PROJECT_OVERRIDE=true /snap/bin/terraform apply plan.tfplan
```

Always plan to a file and apply that file; never apply a stale plan (one rolled the api and
worker back to old images once). Releases of new images without an infrastructure change go
through `gcloud run … update --image` as described in `DEPLOYMENT.md`; update `terraform.tfvars`
to match afterwards so the next plan does not undo the release.

Gates: `make infra-fmt`, `make infra-validate` (both roots), `make infra-test` (offline plan with
mock providers). No CI job ever applies; the owner applies.
