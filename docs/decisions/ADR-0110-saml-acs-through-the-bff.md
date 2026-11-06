# ADR-0110: the SAML ACS is the one cross-site write the web proxy accepts

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt B, task 2)

## Context

The SAML HTTP-POST binding is an auto-submitted form on the customer's IdP page: its Origin is the
IdP (or `null`) and it carries no CSRF token. The web proxy (`web/app/v1/[[...path]]/route.ts`)
refused every non-same-origin write with 403, so a SAML sign-in through the web tier could not
finish.

## Decision

`web/lib/cross-site.ts` allows exactly `POST /v1/sso/saml/acs` with an
`application/x-www-form-urlencoded` body (anything else 415) of at most 256 KB (413), without the
same-origin check. No other route's Origin rule changed. The fix prompt asked for no cookie
forwarding; the code needs the opposite: the API binds the RelayState to the browser through the
`__Host-ai_preauth` cookie (`SameSite=None` for SAML on purpose), so the proxy forwards the usual
auth-cookie allow-list and nothing else. The API already checks RelayState against
`login_states` (unknown, other browser, replayed, expired, wrong protocol) and every assertion id
once (`saml_assertion_ids`); `tests/phase3/test_saml_acs_cross_site.py` now proves those checks
for the SAML path with an IdP Origin (the old tests covered them for OIDC only).

## Consequences

Tests: `web/tests/unit/saml-acs-proxy.test.ts` (7 cases) and the three API tests above.
