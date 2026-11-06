# ADR-0127: only the web service may invoke the api; the web server sends a Google ID token

- Status: Accepted (code); Terraform planned, NOT applied
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 6, the part ADR-0121 left open)

## Context

The api Cloud Run service had internal ingress but `roles/run.invoker` for `allUsers`: anything
inside the VPC (or another Cloud Run service with VPC egress) could call it, with only the
application's own authentication in the way.

## Decision

Terraform (plan): `public_invoker = ["web"]` (the web service stays public behind the load
balancer, which a serverless NEG needs), and `invokers.api = { web = <web service account> }`.
The plan shows `cloud_run_v2_service_iam_member.public["api"]` destroyed and
`invoker["api:web"]` created; nothing else for this task.

Web: every server-side call to the api adds `X-Serverless-Authorization: Bearer <ID token>`
(`web/lib/serverless-auth.ts`). Cloud Run IAM reads that header and leaves `Authorization` to the
application, which matters because the operator proxy already sends the staff session bearer in
`Authorization` (the api's operator auth). The token comes from the metadata server
(`.../service-accounts/default/identity?audience=<API_ORIGIN>`), cached until five minutes before
it expires; `google-auth-library` is **not** a web dependency and was not added. Off Cloud Run
(`K_SERVICE` unset: dev, tests, the local stack) no token is fetched and no header is set. On
Cloud Run a metadata failure fails the call as an upstream failure (never an unauthenticated
call). A browser cannot supply the header: the `/v1` proxy forwards an allow-list of headers and
sets this one itself.

Callers covered (every `apiOrigin()` / `operatorApiOrigin()` fetch): `lib/server-api.ts`
(`serverGet`; the brief's "serverGet.ts"), the `/v1` BFF proxy, `app/api/auth/logout`,
`app/readyz`, the operator proxy `app/api/operator/v1/[...path]`, `app/api/operator/callback`,
`app/api/operator/session`, `lib/operator-session.ts` (mint and revoke).
`web/tests/unit/serverless-auth.test.ts` checks the header on each upstream call, the staff
bearer staying in `Authorization`, the cache, no metadata call off Cloud Run, and fail-closed.

The audience is `API_ORIGIN` (`local.api_url`, the service's deterministic run.app URL), which
Cloud Run accepts without a custom audience.

## Probes

With IAM on, there is no "public path" on a Cloud Run service. Cloud Run's own startup and
liveness probes (`/readyz`, `/healthz` on the api) are made by the platform and are not subject
to IAM, so they keep working. The api's uptime check already goes through the web's `/readyz`,
which now carries the token. Not verified here (needs the project): after applying, in the Cloud
Run console the api's revision must stay Ready and its Permissions tab must list only the web
service account (plus deployer) under Cloud Run Invoker; the uptime check "api uptime" must stay
green; a `curl` from inside the VPC without a token must get 403.
