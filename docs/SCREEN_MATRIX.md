# Screen matrix (UI redesign, 2026-10-11)

> Phase 4C (2026-10-15): the parity suite (`web/e2e/parity/`, `playwright.parity.config.ts`) was removed
> as dead code. The current screen check is `web/e2e/replica/screens.spec.ts` (axe, CSP, overflow at
> 1360 and 375). This page is kept as the record of the 2026-10-11 redesign.

Every KEEP and BUILD screen from `docs/UI_BACKEND_AUDIT.md`, with the parity tests that pin it
(`web/e2e/parity/*.spec.ts`). The suite queries roles, labels and visible text only, so it runs
unchanged on the old and the rebuilt UI.

- **Old UI** shows the result on the old UI (baseline, after the harness fixes below).
- **New UI** shows the result of the same suite on the rebuilt screens.

Run with: `tests/e2e/serve.py` on 8088 (`E2E_WEB_ORIGIN=http://localhost:3001`) and
`npx playwright test -c playwright.parity.config.ts` in `web/`.

| Screen | Parity tests | Old UI | New UI |
|---|---|---|---|
| `/login` | auth: unknown user and wrong password share one message; reveal password; SSO sign-in lands in the workspace; invalid sign-in ID refused | Pass | Pass |
| `/forgot` | auth: confirms without revealing whether the account exists | Pass | Pass |
| `/reset` | auth: a link without its token says it is incomplete | Pass | Pass |
| `/verify-email` | auth: a link without its token says it is incomplete | Pass | Pass |
| Every page | auth: signed-out visitors go to sign in; nonce CSP and security headers; roles: skip link first; signing out ends the session | Pass | Pass |
| `/` Home | client: a question asked on Home is answered there | Pass | Pass |
| `/accounts` | client: only this client's accounts, adding one opens it; roles: viewer cannot add | Pass | Pass |
| `/accounts/[domain]` | client: missing and foreign accounts share one not-found page; research: one confirmed refresh starts exactly one run; downloads build and download | Pass | Pass |
| `/accounts/[domain]/tables/[table]` | research: Facts rows, search, saved view, source; every table opens; row feedback saved | Pass | Pass |
| `/accounts/[domain]/history` | research: signals history opens | Pass | Pass |
| `/chat` | client: a question streams, persists and can be deleted; Stop is offered while streaming; roles: history is private to its member | **Fail** (FIX: the thread list is not reloaded after a turn, so the new thread's button has no name) | Pass |
| `/alerts` | client: the seeded alert shows and a rating is saved | Pass | Pass |
| `/runs`, `/runs/[run_id]` | research: the run is listed; its page shows status and coverage | Pass | Pass |
| `/settings` | settings: research preferences save; branding saves a display name | Pass | Pass |
| `/settings/alerts` | settings: subscribe up persists; allowance in percent | Pass | Pass |
| `/settings/users` | settings: members listed; adding one reports the server's answer | Pass | Pass |
| `/settings/security` | settings: sessions listed with this device marked | Pass | Pass |
| `/settings/sso` | settings: this client's connection is listed | Pass | Pass |
| `/settings/connectors` | settings: offers to connect a source | Pass | Pass |
| `/settings/crm` | settings: off by default; refuses an unmapped preview | Pass | Pass |
| `/settings/support` | settings: a grant request reports the server's answer | Pass | Pass |
| `/settings/model-key` | settings: availability shown, never a key value | Pass | Pass |
| `/usage` | settings: plan allowances in units | Pass | Pass |
| `/audit` | settings: account adds recorded; CSV offered | Pass | Pass |
| Tenant isolation | roles: a second client sees nothing of the first | Pass | Pass |
| `/operator/login` | operator: Google sign-in, names no client; no session goes to sign in | Pass | Pass |
| `/operator` | operator: overview lists clients by id | Pass | Pass |
| `/operator/tenants` | operator: provision and suspend | Pass | Pass |
| `/operator/configuration` | operator: add a flag version; support role cannot change flags | Pass | Pass |
| `/operator/support`, `/operator/failures` | operator: a malformed grant is refused before calling the API | Pass | Pass |
| `/operator/runs`, `/operator/cost`, `/operator/spend` | operator: ask for a grant and a run id | Pass | Pass |
| Operator sign out | operator: ends the staff session | Pass | Pass |

Old UI total: 49 of 50 pass. The one failure is the conversation-list FIX above.

New UI total: 50 of 50 pass on a fresh seed (run of 2026-10-11, 3.5 minutes), with the conversation list fixed. Screenshots of every screen in light and dark at 1440, 1024 and 390 were in `docs/design-review/` (removed in Phase 4C; still in git history before that commit).

## Fixes made to the suite and the harness while taking the baseline

These are test fixes. None of them changes backend behaviour.

- **Port 3001.** The parity web server runs on 3001: the backend's same-origin rule accepts
  development loopback only on 3000 and 3001, and 3000 is the Docker web container.
- **Harness staff verifier.** The dev staff verifier stamped MFA with the wall clock, which is
  ahead of the harness's frozen clock, so every operator step-up check failed. The harness now
  stamps it with its own clock.
- **Synthetic chat provider.** It now accepts the extra arguments the chat route passes
  (`user_id`, `request_id`, `fresh_output`).
- **Origin on direct mutations.** `page.request` mutations now send the same-origin `Origin`
  header that the `/v1` proxy requires.
- **Flag rule.** The test rule is `{"percent": 0}`; the backend accepts only `tenants`, `plans`
  and `percent`.
- **Suspend confirmation.** The test accepts "Suspended" as well as "saved".
- **Overview.** The overview shows the first eight clients, so the test checks for a client id
  rather than the seeded one.
- **Alert preferences reset.** The reset before the test now sends the same-origin header too, so a rerun starts from the digest-only state.
- **Facts, not People.** The synthetic world makes no people call (consent is required), so the
  deep dive test uses the Facts table.

## Findings for the rebuild

- **FIX:** the conversation list reloads after a turn.
- **FIX:** an empty research table shows an empty state instead of a bare header row.
