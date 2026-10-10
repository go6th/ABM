# Operator and release runbook

The supported default deployment is Google Cloud Run through the existing Terraform roots and workflow. An optional web-only Helm chart is available at `deploy/helm/web` for a separately operated GKE cluster. It does not deploy the API, database, task worker, or identity provider. Production actions require the target platform's configured identity, release approval and network policy.

## Local development

1. Install Python 3.11+, `uv`, Node 22+, npm and Docker Compose.
2. Copy `.env.example` to `.env` if present; use the variable inventory in [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md). Never commit secrets.
3. Start the local services with `docker compose up --build` and run `uv run alembic upgrade head` using the development migrator configuration.
4. Generate/check the API contract with `uv run python scripts/export_openapi.py` and `uv run python scripts/gen_web_client.py`.
5. Use the tenant SSO test setup documented in `docs/` for local sign-in. Operator sign-in requires a configured Google Workspace OIDC client and an allowlisted identity with MFA; there is no local operator bypass.

## Readiness and rollout

- `/healthz` checks process liveness. `/readyz` checks configuration, runtime wiring and database connectivity; a non-200 response must keep a revision out of traffic.
- Review the release diff, migration plan, CI result, image digest, SBOM and signed provenance before promoting a revision. Promote the same digest through staging and production using the existing GCP workflow.
- Roll back Cloud Run traffic using the previous healthy revision as documented in [docs/DEPLOY.md](docs/DEPLOY.md). Do not roll back a schema migration unless its down migration is explicitly safe for the live data.

## Optional GKE web chart

- Build and publish the web image with the existing CI image workflow, then deploy by immutable digest using `helm upgrade --install ai-web deploy/helm/web --set image.digest=sha256:... -f <environment-values.yaml>`.
- Configure HTTPS ingress, `operator.redirectUri`, Workspace domain, the External Secrets `ClusterSecretStore`, remote secret keys, API service namespace/labels, and the `pool=api` node pool before rollout.
- The chart mounts operator OAuth values as files, runs UID/GID 10001 with a read-only root filesystem, probes web liveness at `/healthz` and API-backed readiness at `/readyz`, and includes HPA/PDB/NetworkPolicy resources.
- `rollouts.enabled=true` requires the Argo Rollouts CRD/controller. `externalSecrets.enabled=true` requires External Secrets Operator and a configured `ClusterSecretStore`.
- The NetworkPolicy permits the API service, DNS, and TCP/443 for Workspace OAuth. Constrain outbound HTTPS through the cluster's egress gateway/firewall before production use. Do not enable ingress until TLS and proxy trust are configured.

## Operator bootstrap and access

1. The operator identity owner configures the Google Workspace OAuth client and explicit operator email/role allowlists in Secret Manager/runtime configuration.
2. The named operator enables Workspace MFA before being allowlisted. Verify `/operator/v1/session` returns the expected role and fresh MFA state through the protected console.
3. There is deliberately no public operator registration route. Keep the operator allowlist small and reviewed; revoke access by removing the identity from the allowlist and revoking the Workspace session.

## Authentication boundary status

Tenant sign-in currently uses the configured OIDC/SAML broker and server-side session cookies. First-party password registration, email verification, reset, local MFA enrollment and recovery are not implemented. Do not enable self-serve password signup or advertise it as available. The configured production mailer and identity provider must be validated before enabling tenant SSO.

## Incident notes

- **Suspected tenant/session compromise:** disable the affected membership or SSO connection, revoke sessions, preserve append-only audit/auth events, and capture request IDs. Do not copy cookies or bearer credentials into an incident ticket.
- **Operator compromise:** remove the Workspace identity from the allowlist, revoke Workspace sessions, rotate OAuth client credentials in Secret Manager, and review operator audit events and support grants.
- **Auth lockout:** no first-party password lockout flow exists yet. For Workspace accounts, use the identity provider's admin recovery process; do not create a local token or disable MFA.
- **Signing/secret rotation:** rotate through Secret Manager and the owning provider's documented key overlap procedure. Confirm both old/new key handling and rollback before retiring the previous version.
- **Database incident:** follow `docs/runbooks/database-failover.md`; preserve tenant RLS roles and verify `/readyz` and the isolation suite after recovery.

## Known owner inputs before production activation

Set the public app/API origins and cookie host, mail delivery owner, named initial operator, GCP project/region, and payment account/webhook owner. Stripe checkout remains an architecture recommendation and is not wired by this runbook.
