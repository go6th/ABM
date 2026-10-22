# Security

Report vulnerabilities privately to the repository maintainer (GitHub
[@Sakib-11](https://github.com/Sakib-11)). Do not put exploit details, credentials, or tenant data
in issues, pull requests, or discussions.

Tenant isolation is enforced in PostgreSQL with forced row-level security, non-owner runtime roles,
and transaction-local tenant context derived from verified identity. Isolation and public/private
rendering tests are release gates. This is a design guarantee to verify in each deployed
environment, not a substitute for an external security assessment.

## Trust boundaries

- Tenant identity comes from the verified bearer identity or the opaque HttpOnly session cookie.
  The browser cannot select a tenant ID in request parameters. Mutating cookie-authenticated
  requests require the session-bound CSRF token.
- Session listing is scoped to the verified tenant and user. Revocation uses a conditional update
  over both session ID and user ID, returns the same 404 for absent and foreign sessions, and
  records a fixed reason code; no session token or token hash is returned by these APIs.
- Tenant credentials and operator identities are separate planes. The removed staff-password
  shim must not be restored. Operator access uses backend-verified Google Workspace OIDC,
  configured identity allowlists and recent MFA; operator routes accept no tenant principal.
- The Next tenant BFF forwards only the `__Host-ai_session` and `__Host-ai_preauth` cookies plus
  an explicit header allowlist. Operator API errors fail closed and do not return fabricated data.
- The UI validates `/v1/me` from the backend before showing the authenticated workspace. Hiding a
  control is not authorization; every API route still uses the route policy and tenant guard.
- Provider names, broker credit counts and per-call dollar costs are operator-only. Review API
  serializers and export tests whenever a response or export schema changes.

## Threat and mitigation map

| Threat | Current control | Remaining evidence needed |
|---|---|---|
| Cross-tenant IDOR | Verified identity, tenant transaction context, forced RLS, route catalog | Run PostgreSQL isolation suite against the release migration set and live grants |
| Session theft or CSRF | Secure HttpOnly `__Host-` cookie, server-side token hash, expiry/revocation, CSRF | Validate HTTPS/cookie origin and revocation behavior in deployed environment |
| Tenant/operator privilege crossover | Separate route planes, verified operator token, role/MFA checks | Verify deployed allowlists, Workspace MFA and operator audit coverage |
| Auth brute force/account enumeration | First-party login, verify and reset routes: Argon2id with a dummy verify for unknown accounts, one uniform refusal, per-IP and per-account Redis buckets (`config/auth.yaml`), progressive lockout in `local_auth_failure` (5/1 min, 8/5 min, 12/1 h), Cloud Armor rate ban on the sign-in routes (30/min per IP, 10 min ban, `infra/modules/edge`) | Verify the deployed Cloud Armor rule and Redis buckets with a controlled burst on staging; keep self-serve signup off |
| Stale or malformed UI authorization | `/v1/me` schema validation, no-store server request, API authorization remains authoritative | Add browser E2E for role/capability changes and session expiry |
| Proxy header/cookie injection | Explicit BFF cookie/header allowlist; no arbitrary Authorization forwarding | Add BFF integration tests for hostile cookies/headers and upstream outage |
| API data leakage | Tenant/operator schemas and separate route policy | Serializer/export regression tests for provider, cost and broker fields |
| Secret exposure | Secret Manager adapters and write-only connection-secret handling | Verify deployed IAM, secret rotation and log redaction |

## Auth implementation status

Tenant sign-in is either the configured SSO broker (OIDC or SAML) or the first-party local
password flow; both end in the same HttpOnly server-side session (`RUNBOOK.md`, Authentication
boundary). The local flow is shipped and exposed on these anonymous routes
(`src/account_intelligence/api/routes/auth.py`): `POST /v1/auth/login`,
`/v1/auth/email/resend`, `/v1/auth/email/verify`, `/v1/auth/password/reset/request` and
`/v1/auth/password/reset`; password enrollment (`/v1/auth/password/enroll`) is a tenant route
for the signed-in member. Self-serve signup stays off; registration happens through invites.

- Passwords are hashed with Argon2id (`src/identity/local_password.py`, parameters in
  `config/auth.yaml`: time cost 3, 64 MiB, parallelism 2, length 12 to 128, common-password and
  account-context checks). Unknown accounts and malformed stored hashes are verified against a
  random-salted dummy digest, so timing and the response do not reveal whether an email exists.
- Every refusal on login, verify and reset is the same 401 problem; verification and reset tokens
  are single use, hashed (SHA-256) at rest and expire (migrations 0047 and 0048).
- Brute force: per-IP and per-account token buckets in Redis (`src/identity/rate_limit.py`,
  `config/auth.yaml`; per-process in dev and test), progressive lockout recorded atomically by
  `local_auth_failure` (5 failures lock for 1 minute, 8 for 5 minutes, 12 for 1 hour; events
  `login_failed`, `login_locked`), and at the edge a Cloud Armor rate-based ban on the sign-in,
  verify, reset, invite-accept and SSO start routes (30 requests per minute per client, 10 minute
  ban; `infra/modules/edge/main.tf`).
- MFA: an admin may not sign in with a local password while MFA is required (local TOTP is not
  implemented); admins use SSO. There is no MFA enrollment or recovery route yet.

Migration 0047 adds `local_identities` and the single-use hashed auth tokens behind forced RLS;
0048 adds password reset without widening `app_rw`. `app_rw` has no direct access to the
credential tables; its only access is through the reviewed `SECURITY DEFINER` functions owned by
`identity_authenticator` (`NOLOGIN`, `NOBYPASSRLS`, pinned `search_path`). The role and the
functions are covered in `tests/isolation/matrix/matrix.yaml` and
`tests/phase3/test_ws2_local_auth.py`.

| Database role | Runtime login | RLS bypass | Credential access | Owner/review |
|---|---:|---:|---|---|
| `app_rw` | Yes | No | No direct table grants; calls the listed functions only | Normal application role |
| `identity_authenticator` | No | No | Narrow column grants on local identities, tokens, memberships and tenant lifecycle rows; owns the fixed-search-path security-definer functions (`local_auth_lookup`, `local_auth_failure`, `local_auth_success`, `local_auth_verify_email`, `local_auth_issue_token`, `local_auth_reset_email`, `local_auth_reset_password`) | `.github/CODEOWNERS`, migrations 0047 and 0048, isolation matrix |

Still open before local auth is widened: MFA enrollment and recovery (TOTP), refresh rotation
for long sessions, and a production mail adapter with its SMTP credential in Secret Manager
(verification and reset mail is refused, not faked, while `SMTP_HOST` is unset). Never return
credentials or tokens to browser JavaScript, and never enable self-serve signup before those
controls and their integration tests exist.
