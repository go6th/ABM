# Screen to API and capability map

Capabilities come from authenticated `GET /v1/me`; the browser does not maintain a role-to-screen policy. API authorization remains authoritative even when a control is hidden.

| Screen | API surface | Capability / gate | Status |
|---|---|---|---|
| Tenant sign-in | `GET /v1/sso/{slug}/start`, OIDC/SAML callback | Anonymous sign-in flow; tenant comes from configured connection | SSO flow exists; first-party registration/password screens absent |
| Account workspace | `/v1/accounts`, `/v1/runs`, `/v1/me` | `brief:public:read`; run actions require `runs:create` | Existing workspace; capability-based nav added |
| Run detail/progress | `/v1/runs/{id}`, progress, approval and cancel | Capabilities from `/v1/me`; API verifies tenant ownership and action policy | Run details, coverage outcome, approval/cancel actions, ETag polling and recent-run links implemented |
| Chat | `/v1/chat/*` | Authenticated tenant membership and chat route policy | Existing |
| Alerts | `/v1/signals`, `/v1/alerts` | `alerts:manage` for mutations; server policy for reads | Existing |
| Usage and wallet | `/v1/usage`, `/v1/spend/*` | `usage:read` and server-side wallet policy | Existing |
| Audit | `/v1/audit/*` | `audit:read` / `audit:export` | Existing |
| Settings and branding | `/v1/settings/*`, `/v1/branding` | `settings:manage` | Existing |
| Team | `/v1/users/*` | `users:manage` | Existing |
| Operator console | `/operator/v1/*` through operator BFF | Google Workspace allowlist, operator role and fresh MFA | Existing separate plane; no fabricated fallback state |
| Profile/security | `/v1/sso/session`, `/v1/sso/sessions`, logout/logout-all, `DELETE /v1/sso/sessions/{id}` | `sessions:revoke:self`; cookie session and CSRF required for mutations | Active-session list and per-session revoke UI/API implemented; local MFA/password controls absent |

## Current-user response

`GET /v1/me` returns the verified user and tenant identifiers, role, plan, isolation tier, capability names, enabled feature flags, plan limits and MFA state. The Next server layout validates this payload against the generated OpenAPI Zod schema before rendering the authenticated shell. A 401 redirects to sign-in; unavailable or malformed identity metadata fails closed.
