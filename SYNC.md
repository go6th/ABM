# Implementation sync

## Decisions applied

- Keep the repository's Google Cloud Run, PostgreSQL RLS, FastAPI, Next.js, Cloud Tasks and LangGraph deployment architecture.
- Remove the untrusted tenant-staff login shim and its broad-grant staff-account migration.
- Preserve separate tenant SSO sessions and Google Workspace operator OIDC with backend MFA validation.
- Make `/v1/me` the source for client navigation capabilities, plan limits, feature flags and MFA status.
- Never fabricate operator data when the API is unavailable; return an unavailable response with no cached substitute.

## Implementation status

- Backend: `/v1/me` added; route catalog updated; generated OpenAPI and TypeScript schemas refreshed.
- Tenant web: removed localStorage bearer-token handling and staff-login proxy; forwards only the secure session/preauth cookies and an explicit header allowlist.
- Tenant sessions: added a user-scoped active-session API and security screen; revocation is CSRF-protected and tested against cross-user session IDs.
- Research runs: added a dedicated run detail route driven by generated API schemas; it shows the state, outcome and coverage rows, capability-gates approval/cancel controls, links recent runs from the workspace, and polls progress with ETags while pausing in background tabs.
- Local auth: added Argon2id password policy and asynchronous hash/verify primitives, including dummy work for unknown accounts, and an expand-only credential schema. Global identities and tokens are protected by forced RLS and narrow `identity_authenticator` definer functions; `app_rw` has no direct credential-table grants. Added `/v1/auth/login`, same-origin BFF login, SSO-member password enrollment, hashed single-use email verification with a fragment token, resend, shared account rate limits, and tenant selection derived from verified membership. Successful local sign-in creates the existing HttpOnly server-side session and rotates or revokes a prior session. Admin local sign-in remains refused while MFA is required because local TOTP is not implemented.
- Operator web: removed local HMAC session signing and fabricated API fallbacks; operator token is verified by the backend before the BFF sets an HttpOnly cookie.
- Delivery: added web `/healthz` and API-backed `/readyz`, plus an optional digest-pinned Helm chart with HPA, PDB, node placement, ExternalSecret, NetworkPolicy and Argo Rollouts options. Cloud Run remains the supported default.
- Docs: audit, decisions, screen map and operations runbook record current status and owner-dependent production setup.

## Open engineering work

- Self-service tenant registration is still missing. The implemented enrollment attaches a credential to an already authenticated SSO member; the feature-flagged self-service signup default from the prompt is not yet enabled or implemented.
- Password reset/delivery, local MFA enrollment/recovery, refresh-token rotation/reuse detection, and the local TOTP path required for admin sign-in remain open. IP throttling is enforced at Cloud Armor; account throttling uses shared Redis in cloud and bounded in-process buckets in dev/test.
- Production mail adapter and tenant SSO IdP adapter/secret wiring remain deployment prerequisites; the flow fails closed when a mailer is not configured.
- Complete verification of capability mapping against route policies and broader E2E auth/isolation scenarios.
- Add a run page E2E for state, coverage, and role-gated approval/cancel actions.
- Stripe checkout/webhooks, first-party payment ownership, and the roadmap's approval and table workflows.
- Production-domain CSP/cookie configuration, GKE egress/ingress policy validation and named operator bootstrap.

## Architecture divergence

The supplied production roadmap recommends Temporal; Architecture v2.0 specifies Inngest with LangGraph; the repository is already deployed around Cloud Tasks with LangGraph checkpoint ownership. This implementation leaves the run API independent of that choice and keeps the existing stack. A workflow-engine migration needs a separate ADR and compatibility/migration plan.

## Latest verification

- `tests/phase3/test_ws2_local_auth.py tests/phase3/test_ws2_local_auth_api.py tests/unit/test_local_password.py tests/unit/test_identity_rate_limit.py tests/phase3/test_ws2_roles.py`: 22 passed against PostgreSQL 16 + pgvector, including SSO → enrollment → verification → local login, token replay rejection, same-subject login, session rotation, active-membership resolution, lockout, and direct credential-grant denial.
- `tests/isolation/test_matrix.py`: 318 passed against PostgreSQL 16 + pgvector, including the current auth route catalog and all discovered tenant-table coverage.
- Migration 0047 upgrade succeeded; migration lint passed (92 SQL files); migration-matrix discovery passed with the new resolver functions and the `/v1/me`/session routes covered.
- `check_route_catalog.py`, OpenAPI snapshot, generated TypeScript client, Ruff, Mypy and `git diff --check` passed. Web typecheck and ESLint passed.
- Self-service registration, password reset, production email delivery, local MFA/recovery and refresh-token rotation remain incomplete; this work does not close the P0 authentication finding or authorize production launch.
- Credential-boundary integration tests pass against the migrated schema; the complete route-wide tenant isolation probe passed after correcting a stale chat fixture.
