# Pilot onboarding checklist (Phase 3 WS9, PL2)

Status: **ready to run, not run.** "Two pilot tenants live on real accounts" is an exit criterion
that stays **Blocked** until a real pilot is onboarded with this checklist. Nothing here has been
done with a real customer, a real IdP or real accounts.

Rehearse first on synthetic data: `APP_ENV=dev python scripts/seed_pilot.py --accounts 3,5`
provisions two synthetic tenants (`Synthetic Pilot A` and `B`) on the pilot plan with three and
five `.example` accounts. It starts no run and calls no provider.

## Before the kickoff (6th)

| # | Step | Owner | Evidence |
|---|------|-------|----------|
| 1 | Pilot agreement signed; data-processing terms cover the tenant data and notes | Product owner | signed agreement reference |
| 2 | Tenant provisioned: `POST /operator/v1/tenants` (operator role) | Operator | `operator_audit` row `tenant.create` |
| 3 | Plan: `POST /operator/v1/tenants/{id}/plan` `{plan_code: pilot}`; quota overrides only with a reason code | Operator | `plan_assignments` version, tenant audit `operator.plan` |
| 4 | Staff IdP configured (`STAFF_JWT_ISSUER`, `STAFF_JWT_AUDIENCE`, `STAFF_JWKS_URL`); otherwise operator routes answer 503 | Platform | `/operator/v1/tenants` answers 200 for an operator token |
| 5 | Customer IdP: OIDC issuer and client, or SAML metadata; verified email domains; group-to-role map; JIT on or off ([VERIFY] the customer's IdP details) | Customer IT + 6th | `/v1/sso/connections` entry; one test sign-in |
| 6 | First admin added; MFA confirmed (admins need `amr` with MFA) | Customer admin | `membership_events` `added`, `auth_events` `signed_in` |

## Kickoff (with the customer admin)

| # | Step | Evidence |
|---|------|----------|
| 7 | Settings: platform lanes, people regions, exclusions (companies, people), the pursuing client, branding name | `tenant_settings_versions` version 1 |
| 8 | Members invited with roles (admin, analyst, seller, viewer) | `/v1/users` |
| 9 | Connectors (optional, D-65): Drive folders named by the customer; Salesforce or HubSpot read scopes only; Gmail and call notes stay off | `connector_grants`, `connector_access_log` |
| 10 | Three to five accounts added (`POST /v1/accounts`) and the first runs started at standard depth | `usage_events` `account_added`, `run_started` |
| 11 | Walk through the tables, the source drawer, saved views and exports | – |
| 12 | Explain feedback (Useful / Not useful with a reason) and what is measured (D-68): marking or copying a first-call question, opening or noting a Confirmed fact, and downloading an export that contains it. Identifiers only; notes are the customer's data. | – |
| 13 | Explain support access: 6th sees nothing without a grant; grants are time-boxed, read-only, revocable, and every support action is listed under Support access | `/v1/support/grants` |

## During the pilot (weekly)

* Feedback review with `docs/phase3/feedback_review_template.md`.
* Metric report with `docs/phase3/usefulness_metric_report_template.md` (`GET /v1/usefulness/metric`;
  a rate on a base below `config/pilot.yaml` `min_base` is reported as "small base", never as a
  finding).
* Usage against the plan (`GET /v1/usage`); the 80% notice is sent once per period.

## Exit of the pilot

* Customer decision recorded; for a deletion request, the two-person procedure in
  `docs/runbooks/deletion_request.md`.
