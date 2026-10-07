# Support and impersonation procedure (D-66, ADR-0056)

1. The customer asks for help. The 6th support engineer never has standing access.
2. The tenant admin opens Settings > Support access, names the engineer, picks a reason
   (`customer_request`, `incident_investigation`, `onboarding_help`, `data_question`) and a
   duration (read at most 120 minutes; proposal), and confirms with a fresh MFA sign-in.
3. The engineer calls the view-as routes (`/operator/v1/view/*`) with their staff token and the
   grant id in `X-Support-Grant`. Responses carry `X-Support-View: read-only`; the console shows a
   banner. View-as has no export, spend, cap, key or user route.
4. A write needs a second, separate `write` grant (at most 30 minutes; proposal).
5. Every call, allowed or refused, appears under Support actions for the tenant and in its audit.
6. The admin can revoke at any time; expiry is enforced by the server on every request.
7. There is no break-glass path. If the tenant cannot grant (for example its admin is locked out),
   support works from content-free operator data only (`operator_tenant_overview`) and the
   customer's own description.
