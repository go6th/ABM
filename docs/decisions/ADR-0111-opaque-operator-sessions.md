# ADR-0111: the operator console keeps an opaque session, never the Google ID token

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt B, task 3)

## Context

`web/app/api/operator/callback/route.ts` (and the dev sign-in `POST /api/operator/session`) stored
the raw Google ID token as the staff session cookie. That token is a Google credential usable
anywhere its audience is accepted, it cannot be revoked by sign-out, and it lives until Google's
expiry.

## Decision

The API owns staff sessions (the web tier has no database). After the web tier verifies the IdP
token (`GET /operator/v1/session`, fresh MFA required), it calls `POST /operator/v1/sessions` once
with it; the API mints `ops_` + 256 random bits, stores only the SHA-256 in `operator_sessions`
(migration 0099, staff plane, no tenant) and returns the token with its absolute end. The cookie
(`HttpOnly`, `Secure`, `SameSite=Strict`) holds that token and the operator proxy presents it as
the bearer; `api.operator.staff` resolves it. Idle 30 minutes and absolute 8 hours follow
`config/session.yaml`; a session token can never mint another session, so the absolute limit
holds; sign-out calls `DELETE /operator/v1/sessions/current`. The fix prompt placed the exchange
in the BFF; with no database there, the API-side session is the equivalent.

## Consequences

Every staff route still requires an MFA sign-in within `step_up_window_s` (10 minutes, owner
decision 2026-10-15); that rule, not the 8-hour limit, sets how long the console works between
Google sign-ins, exactly as before. Tests: `tests/api/test_operator_sessions.py` (mint, bearer,
hash only, no re-mint, forged token, idle, absolute, sign-out) and
`web/tests/unit/operator-oauth-routes.test.ts`, `operator-session-route.test.ts`.
