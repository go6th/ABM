# Status

All roadmap section 11 requirements (Cross-cutting workstreams) have been built and verified via code.
The platform now has complete coverage across data model conformity, connector contracts, quality gating, retention and privacy policies, security controls, cost rollups, success metrics tracking, and risk management.

Current phase: Ready for beta gate / Phase B.

## ABM agent layer (branch `feat/abm-agents`, 2026-10-08)

The ABM Account Research skill v1.8 runs as agents inside the platform: eight steps, three explicit
run modes (`full` default, `pull`, `build`), guards G1 to G17 enforced in code, the mandatory sense
check before build, approvals for unapproved spend, consent before people pulls, internal history
off by default, and the Internal Only file. **Built guide-only: the skill archive is absent**, so
checklist reconciliation (C31), lessons, runbook prompt text, sense-check questions and the consent
text are TODO (OI-ABM-3). The ABM graph runs on the production runner and adapters for Phase 1
only and Phase 2 only runs, and for full runs behind the `abm_agents` tenant flag (OI-ABM-1,
ADR-0100); a few bindings remain open (OI-ABM-4) and most coverage rows have no grading attribute
class yet (C38). Details: `docs/abm/README.md`, BUILD_LOG.

## Separate client and operator panels (2026-10-08)

The customer workspace and operator console now use separate route groups and shells. The staff
console has a Google Workspace authorization-code flow and connects its available screens to the
generated backend contract. Tenant administration and read-only support access are grant- and
role-checked by the API.

The sign-in screens now use a simpler centered flow, with explicit links between customer and staff
sign-in. The customer dashboard shows live account totals, search, add-account and loading/empty/error
states. The operator overview summarizes only values returned by the operator API. No sample account
or tenant data is embedded in the screens.

Local web typecheck, lint and unit tests pass. Backend identity unit tests pass. PostgreSQL operator
integration tests still need a working local database credential. Production sign-in still needs
the Google OAuth client, registered callback, Workspace domain, Rajesh's exact email allowlist
entry and enabled MFA claims. Screens without backend contracts are explicitly unavailable in
production. See the operator route map at docs/frontend/operator-route-map.md and Google Workspace
setup at docs/phase3/operator_google_workspace.md.

Production build and standalone smoke checks pass. Playwright visual review covered both dashboards
using temporary empty API responses; axe found no WCAG 2.1 A/AA violations on either login or the
dashboard empty states. Updated sign-in screenshots are in docs/frontend/.
