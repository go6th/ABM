# ADR-0052: In-app identity broker for OIDC and SAML (D-62)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

Tenants sign in through their own IdP. Hand-rolled signature or XML handling is forbidden. API v1
already verifies a platform token (`sub`, `tenant_id`, `role`).

## Decision

* `identity.broker` behind a port: OIDC with PyJWT (RS256/ES256 only, issuer, audience, expiry,
  nonce, PKCE S256, injected clock) and SAML with `signxml` (response and assertion signatures,
  only signed elements read, DOCTYPE refused, `InResponseTo`, audience, recipient, time window,
  assertion id replay cache in `saml_assertion_ids`).
* The tenant comes from the tenant's own IdP connection (by slug, then the pending login state bound
  to the browser), never from a claim. Membership by `(connection, subject)`; JIT only when the
  connection allows it, the email domain is verified and a group maps to a role.
* Server-side sessions (`auth_sessions`, token hash only, `__Host-` cookie, idle and absolute
  timeouts, rotation at sign-in, revoke-all); CSRF token = HMAC(session id) on unsafe cookie
  requests; outbound IdP calls through an SSRF-guarded client.
* A managed IdP service may replace the adapter later (C4); the port stays.

## Consequences

Proven by the WS2 positive and negative suites against fake IdPs (`tests/fakes/idp.py`). Not
proven: a real IdP, real SAML metadata, real key rotation ([VERIFY] in docs/open-items.md).
Timeouts in `config/session.yaml` are proposals.
