# Operator and release runbook

The deployment is Google Cloud Run through the Terraform roots in `infra/` and the release steps
in `DEPLOYMENT.md`; the detailed cloud procedures (one-time setup, rollback, secret rotation,
restore, post-deploy checks) are in `docs/deploy/RUNBOOK.md`, and every alert names its runbook in
`docs/runbooks/`. Production actions require the target project's configured identity, release
approval and network policy.

## Local development

1. Install Python 3.11+, `uv`, Node 22+, npm and Docker Compose v2.
2. Copy `.env.example` to `.env`; the variable inventory is [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md). Never commit secrets.
3. `make up` starts the stack and runs the migrations (`compose.yml`, service `migrate`).
4. After an API change run `make contract` (OpenAPI snapshot, generated web client, route catalog, wiring inventory).
5. Local sign-in uses the synthetic identity provider (`ops/dev/fake_idp.py`) or the local password flow. Operator sign-in requires a configured Google Workspace OIDC client and an allowlisted identity with MFA; there is no local operator bypass.

## Readiness and rollout

- `/healthz` is liveness (the process answers). `/readyz` checks configuration, runtime wiring,
  the database and the schema revision (`EXPECTED_REVISION`); a non-200 keeps a revision out of
  traffic, and SIGTERM flips it to 503 so no new work is routed to a stopping instance.
- Before promoting a revision review the diff, the migration plan, the CI result and the image
  digests. Promote the same digests through staging and production.
- Roll back Cloud Run traffic to the previous healthy revision (`docs/deploy/RUNBOOK.md`). Do not
  roll back a schema migration unless its down migration is explicitly safe for the live data.
- After a release, write the deployed digests into `infra/envs/staging/terraform.tfvars`: Terraform
  sets the Cloud Run images, and a plan made from stale values would roll the services back.

## Operator bootstrap and access

1. The operator identity owner configures the Google Workspace OAuth client and the explicit
   operator email allowlists (`STAFF_*` settings, Secret Manager).
2. The named operator enables Workspace MFA before being allowlisted. Verify `/operator/v1/session`
   returns the expected role and fresh MFA state through the protected console.
3. There is deliberately no public operator registration route. Keep the operator allowlist small
   and reviewed; revoke access by removing the identity from the allowlist and revoking the
   Workspace session.

## Authentication boundary

Tenant sign-in is either the configured SSO broker (OIDC or SAML) or the first-party local
password flow (Argon2id, email verification, reset, rate limits), both ending in the same
HttpOnly server-side session. Local sign-in for an admin is refused while MFA is required and
local TOTP is not implemented; admins use SSO. Keep self-serve signup disabled until the owner
decides otherwise.

## Incident notes

- **Suspected tenant or session compromise:** disable the affected membership or SSO connection,
  revoke sessions, preserve the append-only audit and auth events, capture request ids. Never copy
  cookies or bearer credentials into a ticket.
- **Operator compromise:** remove the Workspace identity from the allowlist, revoke Workspace
  sessions, rotate the OAuth client credentials in Secret Manager, review operator audit events and
  support grants.
- **Secret rotation:** rotate through Secret Manager with the owning provider's key-overlap
  procedure (`docs/runbooks/secrets.md`); confirm old and new key handling before retiring the
  previous version.
- **Database incident:** `docs/runbooks/database.md` and `database-failover.md`; preserve the tenant
  RLS roles and verify `/readyz` and the isolation suite after recovery.
- **A research that stopped:** `docs/runbooks/stuck-runs.md`; the structured log event
  `run.finished` carries `stop_reason` and `state`.

## Owner inputs still open before production

The public domain and cookie host, the mail delivery credentials (the SMTP app password), the
named initial operator, the production project and billing, and the payment account. Production
is prepared in `infra/envs/prod` and deployed only by hand through `deploy-prod.yml`.
