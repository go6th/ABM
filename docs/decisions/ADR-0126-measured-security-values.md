# ADR-0126: the "proposal" security values are pinned by tests against the enforcing code

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 5)

## Context

`config/session.yaml`, `config/rate_limits.yaml`, `config/auth.yaml` and the step-up list in
`config/roles.yaml` say `status: proposal`. Nothing proved that the numbers in them are what the
running code enforces.

## Decision

`tests/unit/test_live_security_values.py` pins the live values and drives the enforcing code:

| Value | Live | How it is proven |
|---|---|---|
| Customer session cookie | `__Host-ai_session`, Secure, HttpOnly, Path=/, SameSite=Lax, no Domain | `api/routes/auth._cookie` output |
| Idle timeout | 30 minutes | `identity.sessions.load`: alive at 29:59 after the last request, ended at 30:00, never back |
| Absolute timeout | 8 hours | a request every 20 minutes keeps it alive until 7:59:59, ended at 8:00:00 |
| "Keep me signed in" cookie | Max-Age 28800 (on), no Max-Age (off) | same |
| Step-up window | 10 minutes | `api.guard.authorize` passes a 9-minute-old MFA sign-in, refuses 10 and 60 minutes (`step_up_required`) |
| Routes that need fresh MFA | **27** (matches the audit PDF), listed in the test | counted from the route catalogue (`ROUTE_POLICIES` x `needs_step_up`) |
| Per-user buckets (9) | as in `rate_limits.yaml` | each refuses request capacity+1 with 429 and a Retry-After of one refill, in a real Redis (the local stack's, db 15), through `identity.rate_limit.consume_user` |
| Sign-in account bucket | 10, 0.1/s | same, through `consume(action="login")` |
| Sign-in per-IP bucket | 30, 0.5/s | in the cloud the api skips it (its peer is the web server); the Cloud Armor rule in `infra/modules/edge` carries the same numbers (30 per 60 s), which the test checks |

## Findings

* One value is not enforced by anything: `roles.yaml` lists `request_deletion` for step-up, but no
  tenant route uses that action (deletion is requested on the operator plane). The test for it is
  `xfail(strict=True)` so the gate stays green and the gap stays visible.
* The Redis-backed tests skip when no Redis answers on `localhost:6379` (CI provides one in the
  integration job; check that it does before relying on them).
* The values stay proposals until the owner signs them off; changing one now fails a test, which
  is the point.
