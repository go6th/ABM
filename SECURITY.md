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
| Auth brute force/account enumeration | No first-party password endpoint is currently exposed | Before local auth: distributed IP/account limits, uniform responses, lockout and Argon2id |
| Stale or malformed UI authorization | `/v1/me` schema validation, no-store server request, API authorization remains authoritative | Add browser E2E for role/capability changes and session expiry |
| Proxy header/cookie injection | Explicit BFF cookie/header allowlist; no arbitrary Authorization forwarding | Add BFF integration tests for hostile cookies/headers and upstream outage |
| API data leakage | Tenant/operator schemas and separate route policy | Serializer/export regression tests for provider, cost and broker fields |
| Secret exposure | Secret Manager adapters and write-only connection-secret handling | Verify deployed IAM, secret rotation and log redaction |

## Auth implementation status

Tenant sign-in currently depends on the existing OIDC/SAML broker and its configured production
IdP adapter. First-party email/password signup, verification, reset, MFA enrollment and recovery are
not exposed yet. Migration 0047 adds `local_identities` and single-use hashed auth tokens behind
forced RLS. `app_rw` has no direct access; its only access is through the reviewed functions owned
by `identity_authenticator` (`NOLOGIN`, `NOBYPASSRLS`). The role and outputs are covered in
`tests/isolation/matrix/matrix.yaml` and `tests/phase3/test_ws2_local_auth.py`.

| Database role | Runtime login | RLS bypass | Credential access | Owner/review |
|---|---:|---:|---|---|
| `app_rw` | Yes | No | No direct table grants; calls listed functions only | Normal application role |
| `identity_authenticator` | No | No | Narrow column grants on local identities, tokens, memberships and tenant lifecycle rows; owns fixed-search-path security-definer functions | `.github/CODEOWNERS`, migration 0047 and isolation matrix |

`local_password.py` provides Argon2id hashing/verification, dummy verification for unknown accounts,
and local common-password and account-context checks. Login, registration orchestration, distributed
rate limiting, MFA enrollment/recovery, password reset, refresh rotation and a production mail
adapter still need implementation before local auth may be enabled. Never return credentials or
tokens to browser JavaScript, or enable self-serve signup before those controls and integration
tests are in place.
