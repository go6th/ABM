# Decisions and owner questions

**Gate:** Implementation decisions, 2026-10-09. The user authorized implementation using the prompt's defaults. External deployment and account-owner details remain unconfigured; no operator or payment credentials are stored here.

## Settled from repository evidence / task instruction

| Topic | Decision | Basis |
|---|---|---|
| Tenant roles | Preserve the code's backend-defined `admin`, `analyst`, `seller`, and `viewer` roles. Do not remove `analyst`; it is present in `identity.roles`, route policies, and existing web/operator work. | Running code and migrations have priority; `config/roles.yaml`, `src/identity/roles.py`, `src/account_intelligence/api/policy.py`. |
| Deployment | Keep Cloud Run as the supported default. Provide the requested optional web-only Helm chart for GKE; it does not imply a migration of API/worker/database or automatic production readiness. | Existing GCP Terraform/workflows and task's Helm deliverable. |
| Authentication | Remove unsafe staff login shim. Retain tenant SSO session flow and separate Google Workspace OIDC operator flow. Implement a local issuer using Argon2id and database-resolved tenant memberships; keep signup disabled until verification, abuse controls, MFA and session flows are complete. | Prompt's default, existing identity infrastructure and audit findings. |
| Client-safe data | Tenant APIs and exports must omit provider/broker names, per-call dollar costs and broker credit counts; tenant-facing wallet credits remain available. Enforce in serializers and tests; provider costs stay in operator plane. | Explicit task invariant and product source. |
| Audit sequence | Continue with implementation using recommended defaults after user explicitly authorized work. Keep production deployment gated on real domain, mail delivery, operator ownership and payment account details. | User's follow-up authorized changes across frontend and backend. |

## Owner-dependent production setup

| Decision | Owner answer needed | Prompt default if unanswered |
|---|---|---|
| First platform operator | Name the accountable owner and initial operator identity/provisioning procedure. | CLI-only bootstrap; identity owner and MFA required. |
| Signup | Self-serve registration or invite-only onboarding? | Self-serve behind a feature flag; email verification required before first run/top-up. |
| Identity provider | Local issuer now or external IdP as the primary auth system now? | Local issuer now, SSO-ready. |
| Production domain | Public app/API origins and cookie host. | None; cannot infer a secure production origin. |
| Payment provider | Which provider/account owns checkout and webhook configuration? | Stripe checkout for top-ups. |

## Deferred implementation decisions

- Formal reconciliation of Temporal (roadmap) vs Inngest + LangGraph (Architecture v2.0) should happen only if a future migration is proposed. The current practical recommendation is to retain Cloud Tasks + LangGraph because that is the running implementation and moving engines adds migration risk without enabling a missing UI/API capability.
- Whether the existing SSO broker stays the default for established tenants while local credentials are introduced; settle during auth contract design after the owner chooses the primary identity strategy.
- Exact endpoint/contract path migration from `docs/api/openapi.json` to `/contract/openapi.json`; preserve compatibility until clients and CI are updated together.
- Fix and review the staff auth work already present in the dirty working tree before any production exposure. This audit did not alter those user changes.
