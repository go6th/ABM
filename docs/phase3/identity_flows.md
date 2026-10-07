# Identity flows (D-62, ADR-0052)

Values below are from `config/session.yaml` (all proposals).

## OIDC sign-in

1. `GET /v1/sso/{slug}/start?return_to=/path`: the slug names the tenant's IdP connection. The
   server stores a login state (hash of a 256-bit `state`, PKCE verifier, nonce, `return_to`
   checked to be a same-origin path) bound to a pre-auth cookie (`__Host-ai_preauth`) and
   redirects to the IdP. States expire after 10 minutes and are single-use.
2. `GET /v1/sso/oidc/callback?state&code`: the state must exist, be unexpired, unconsumed and
   bound to this browser; the code is exchanged with the PKCE verifier through the SSRF-guarded
   client; the ID token is verified (RS256 or ES256 only, issuer, audience, expiry, `iat` skew
   60 s, nonce). The subject maps to a membership of THIS connection's tenant; JIT creates one
   only when enabled, the email domain is verified and a group maps to a role; an admin must carry
   `amr` with MFA.
3. A new session (old session id rotated away): `__Host-ai_session` (Secure, HttpOnly, Path=/,
   SameSite Lax), token stored as a hash; idle 30 min, absolute 8 h. CSRF token = HMAC(session id),
   sent as `X-CSRF-Token` on unsafe cookie requests.

## SAML sign-in

Same start; the IdP posts to `/v1/sso/saml/acs` (the pre-auth cookie for SAML is `SameSite=None`
so the cross-site POST carries it). The response and assertion signatures are verified with
`signxml` against the connection's certificate; only signed elements are read; DOCTYPE refused;
`InResponseTo` must equal the pending request; audience, recipient, `NotBefore`/`NotOnOrAfter`
(60 s skew); the assertion id is recorded (`saml_assertion_ids`) and a replay refused.

## Refusals

Every refusal is a fixed reason code in `auth_events` (`login_failed`) and the metric
`auth_signin_refusals_total{outcome}`; the browser sees a generic error page. Runbook:
`docs/runbooks/sso-outage.md`.

## Sessions and membership

* Membership is re-checked through a 60 s cache: removal, suspension and role changes act within it.
* `POST /v1/sso/logout` ends this session; `POST /v1/sso/logout-all` ends every session of the user.
* Step-up: user, SSO, connector, audit export, support grant, deletion and cap actions need an MFA
  sign-in within the last 10 minutes; otherwise 403 `step_up_required`.

## Tests

`tests/phase3/test_ws2_*`: positive flows; wrong issuer, audience, expired, replayed assertion,
missing `InResponseTo`, signature wrapping, unsigned, altered tenant claim, unknown user, disabled
JIT, unverified domain, state and PKCE mismatch, open redirect; fixation, CSRF, timeouts,
revoke-all, step-up.
