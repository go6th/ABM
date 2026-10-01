# ADR-012 — Auth provider: external IdP with JWT tenant/role claims

Date: 2026-10-01
Status: Accepted

## Context
Self-serve needs SSO, invites and MFA without building identity from scratch. Candidates:
WorkOS, Clerk, self-managed Keycloak.

## Decision
Use an external IdP that issues short-lived RS256 JWTs carrying `sub`, `tenant_id` and
`role` claims. The API verifies signature/issuer/audience/expiry and re-checks membership in
the database with a 60 s cache so revocation takes effect within about a minute. The tenant
id always comes from the token, never from the payload (attack A1).

## Consequences
Vendor lock is limited to identity; the JWT contract is provider-neutral. Provider choice
(WorkOS vs Clerk) is a procurement decision, not an architectural one; V4-style verification
applies before sign-off.

## Revisit if
A launch customer requires on-prem SAML federation the provider cannot serve — then front
the provider with Keycloak.
