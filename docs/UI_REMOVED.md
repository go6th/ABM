# UI removed in the light and dark rebuild

Each row was re-checked against `docs/api/openapi.json` before removal (see the DELETE re-read
in `docs/UI_BACKEND_AUDIT.md`). Nothing here was backed by a working operation.

## Screens and elements

| Removed | Why | Replacement |
|---|---|---|
| `/operator/review` (Review queue) | No operator review queue operation or model | None |
| `/operator/monitors` (Platform monitors) | Readiness only; no authenticated operator health, SLO or telemetry operation | None |
| `/operator/audit` (Audit explorer) | The operator audit store has no read operation | None |
| `/operator/announcements` (Status and notices) | No announcement operation | None |
| `OperatorNotConnected` and the "preview" navigation entries | Only served the four placeholders above | None |
| `/design` (component catalogue) | Static page with no operation, public in production | `/__design`, development builds only |
| Login "Resend verification email" button text with an arrow, all-caps kickers ("SECURE WORKSPACE ACCESS", "ACCOUNT INTELLIGENCE", "RESEARCH WITH CONFIDENCE") | Copy rules: no arrows on buttons, no all caps | Sentence case copy |

## Legacy styling and code

| Removed | Notes |
|---|---|
| `web/app/globals.css` | 1,031 lines of legacy element and page rules |
| `web/design/tokens.css`, `tokens.ts`, `tokens-css.ts`, `build-tokens.ts`, `check-contrast.ts`, `contrast.ts` | Old token pipeline; `ui/tokens.css` is hand-written and tested by `tests/unit/ui-tokens.test.ts` |
| `web/design/system.css`, `web/design/panels.css` | Old component and panel styles |
| `web/design/components/index.tsx` | Old primitives; `DataTable` and `Input` moved to `web/ui/` with the same API |
| `web/design/ws/ws.css` | The Evidence workspace styles, superseded by `web/ui/*` |
| `web/design/fonts/` and the `@fontsource-variable/*` packages (Hanken Grotesk, Source Serif 4, Fraunces, Inter, JetBrains Mono) | The system font stack plus one self-hosted Inter variable file |
| `web/public/theme-init.js` | Forced light mode with a script; the theme now comes from the `ai_theme` cookie on the server |
| `web/components/AccountNavigation.tsx`, `SignOutButton.tsx` | Old account tabs and sign-out button; the Deep dive tabs and the account menu replace them |
| `web/components/Progress.tsx`, `Feedback.tsx`, `SignedIn.tsx`, `web/components/ws/States.tsx` | Moved to `web/ui/RunProgress.tsx`, `ui/Feedback.tsx`, `ui/SignedIn.tsx` and `ui/primitives.tsx` with the new markup; same behaviour |
| `web/tests/unit/ws-tokens.test.ts` | Tested the deleted token pipeline; `tests/unit/ui-tokens.test.ts` covers the new tokens |
| Liquid Glass and Claude-styled layers (`components/motion/*`, `ClientShell.tsx`, `AdminOverview.tsx`, `design/motion.css`, `design/claude.css`, `ThemeToggle.tsx`) | Removed earlier on this branch (b865997) |

The search that proves nothing references the removed code is recorded in the final report.
