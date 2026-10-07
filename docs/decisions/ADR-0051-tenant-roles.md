# ADR-0051: Tenant roles and the role-by-action table (D-61)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The roadmap lists four tenant roles (admin, analyst, seller, viewer); ARCH section 9 lists three.
Tenant identity must come only from a verified token or session, and IdP groups vary per tenant.

## Decision

* Four tenant roles. `analyst` = everything a `seller` can do, plus editing shared saved views and
  tracker notes; never users, SSO, billing, connectors or audit. `seller` adds accounts, starts,
  approves and cancels runs, configures alerts. `viewer` reads public output and gives feedback.
  `admin` everything, including audit, users, SSO, connectors and support grants.
* One table, `config/roles.yaml` (`status: proposal`), read by `identity.roles`; routes name an
  action (`api.policy.tenant_route`), never a role list. Step-up (MFA sign-in within
  `step_up_window_s`) for user, SSO, connector, audit export, support grant, deletion and cap actions.
* IdP groups map to roles per tenant (`idp_connections`); **no mapping means no access**; the last
  active admin cannot be removed or demoted; nobody changes their own role.
* 6th staff roles `operator` and `support` live on the operator plane (staff IdP, `/operator/v1`) and
  are never tenant roles; the route catalog check refuses an operator route with tenant roles.

## Consequences

Proven by `tests/phase3/test_ws2_roles.py` (a hand-written oracle of the table, last admin,
self-promotion, operator names refused as tenant roles) and the route catalog check. Needs the
product owner's sign-off; the analyst boundary is a proposal.
