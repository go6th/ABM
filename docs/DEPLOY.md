# Deployment preparation

This repository targets Google Cloud, but this checkout is **not release-ready**. The local
Compose file starts PostgreSQL, the Cloud Storage and Cloud Tasks emulators, and Redis; it does not
start the web app, a worker, a scheduler, a fake identity provider, or provider/LLM sinks. The
Terraform stack currently defines the API and worker from one image. Do not treat a successful image
build or `terraform validate` as a deployment approval. See [RELEASE_REPORT.md](RELEASE_REPORT.md)
for the gates that remain open.

## Owner inputs required before a staging deployment

- Separate staging and production project IDs, region, billing ownership, and data residency.
- Terraform state bucket and a reviewed database tier/availability choice.
- Repository name and GitHub Workload Identity Federation provider and deployer service account.
- A pinned, scanned image digest and an approved migration execution path.
- Secret Manager values and provider registrations; Workspace OAuth client, callback, domain,
  operator allowlist, and MFA claims for the staff plane.
- A decision on the production OIDC provider and user, analyst, seller, viewer, and operator roles.
- Human approval from the configured GitHub `staging` environment reviewers.

Do not place secret values in Terraform variables, `.env.example`, command arguments, or Git.
Terraform provisions Secret Manager references; secret values are entered by the owner after review.

## Validation before any cloud action

The following checks are local and do not authenticate to Google Cloud or create resources:

```bash
make infra-fmt
make infra-validate
uv run --frozen python scripts/preflight_deploy.py --env staging
```

The preflight reads required `TF_VAR_*` inputs and checks the production Terraform Secret Manager
references. It prints names and failures only, never supplied values. It does not prove credentials,
permissions, provider keys, or cloud readiness.

## Human-run staging sequence

Only after the release report's blocking gates are resolved and the owner has reviewed the plan:

1. Create the staging project, enable billing, create a restricted state bucket, and configure the
   GitHub OIDC/WIF provider and deployer service account. Follow
   [bootstrap.md](runbooks/bootstrap.md).
2. Add the required `STAGING_*` repository variables and protect the GitHub `staging` environment
   with named reviewers. The workflow has no deploy job until its WIF provider variable is set.
3. Set non-secret `TF_VAR_project_id`, `TF_VAR_github_repository`, `TF_VAR_image` (an immutable
   `@sha256:` digest), `TF_VAR_db_tier`, and `TF_STATE_BUCKET` in the deployment environment. Run
   `scripts/preflight_deploy.py --env staging` and review its output.
4. Run Terraform from `infra/envs/staging`: `terraform init` with the approved GCS backend,
   `terraform plan -out=staging.plan`, then have the infrastructure owner review that plan. Apply
   only through the approved change process.
5. Enter provider, database, and model secrets into Secret Manager through the approved secret
   workflow. Confirm workload identities have only the required access.
6. Build, scan, and publish the exact reviewed image digest. Run migrations as a separate, reviewed
   job before any service rollout. The current repository does not yet provide separate validated
   API, worker, web, and migrator images/jobs; resolve this before rollout.
7. Deploy API, worker, scheduler, and web only after the service split and local end-to-end stack
   gates pass. Verify `/healthz`, `/readyz`, same-origin browser-to-API access, authentication,
   tenant isolation, and a synthetic run. Do not use real tenant data in smoke tests.
8. Record image digest, migration revision, readiness output, smoke-test run ID, approver, and
   rollback target in the release record.

The existing `make deploy-staging` command only prints a legacy command template. It does not
deploy. The GitHub workflow is gated by branch, required jobs, WIF variables, and the protected
environment, but its image and service rollout have not passed the complete deployment gates.

## Rollback

For a service rollback, route traffic to the previously approved Cloud Run revision only after
checking migration compatibility. The current migrations are not all proven safe for rollback with
live data; use forward fixes for irreversible data changes. Restore the database only under the
incident commander and database owner's approval, following [restore.md](runbooks/restore.md).

No cloud plan, apply, deploy, secret write, or real credential check was run for this audit.
