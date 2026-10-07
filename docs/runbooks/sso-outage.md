# Runbook: SSO sign-in refusals or an IdP outage (ticket)

Owner: TODO (identity owner, docs/oncall/rotation.md)

## Symptom
`sso_signin_refusals` fired: `auth_signin_refusals_total` above the threshold (proposal: 20 in 15
minutes), labelled by reason code (`state_expired`, `unknown_connection`, `bad_signature`,
`not_a_member`, `admin_requires_mfa`, ...). Users of one tenant report they cannot sign in.

## Impact
Members of the affected tenant cannot open the app. Existing sessions keep working until their
idle or absolute limit (`config/session.yaml`); no data is exposed by a refusal.

## First checks
1. The reason label on the metric (dashboard). One reason dominating names the cause:
   `bad_signature` or `unknown_key` means the IdP rotated keys; `state_expired` means slow or
   looping redirects; `not_a_member` or `group_unmapped` means directory or mapping changes.
2. Run `queries/tenant_overview.sql` to confirm the tenant is not suspended (a suspended tenant
   refuses every sign-in by design).
3. Run `queries/operator_actions.sql` for recent operator changes to that tenant.
4. With the tenant admin (never by reading tenant data): their IdP status page, recent
   certificate or client secret rotation, group changes.

## Mitigation
Key rotation: the next sign-in refetches the JWKS (OIDC); SAML needs the tenant admin to upload the
new certificate under Single sign-on. Mapping changes: the tenant admin fixes the group-to-role
map. Never create a session by hand, never relax signature, audience, issuer or replay checks, and
never add a member on the tenant's behalf without a support write grant.

## Escalation
Ticket per docs/oncall/severity.md; page the identity owner if every tenant is affected (likely our
side: clock, secrets, egress to IdPs).

## Verification
Refusals return to baseline; a member of the tenant signs in; `auth_events` shows `signed_in`.

## Follow-up
Record the cause; if the IdP behaved outside the documented flow, add a fake-IdP case to
`tests/fakes/idp.py` and a negative test.
