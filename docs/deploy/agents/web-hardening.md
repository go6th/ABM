# Agent 6 web-hardening: report

Branch `agent/web-hardening` (from `feat/replica` a182f56). Everything below is under `web/`
unless a path says otherwise. `web/lib/gen/` was not touched.

## Checks (from `web/`)

| Command | Before | After |
| --- | --- | --- |
| `npx tsc --noEmit -p .` | 0 errors | 0 errors |
| `npx eslint .` | 0 errors, 1 warning (`.probe.mjs`) | 0 errors, 0 warnings |
| `npx vitest run` | 13 files, 59 tests (7 files excluded) | 22 files, 120 tests, 0 excluded |
| `npm run build` | not run | exit 0 |
| `find .next/static -name '*.map'` | not run | 0 files |
| `npm audit` | 5 high | 5 high (see L15) |

The CSP string is unchanged; `tests/unit/security-headers.test.ts` pins the exact string (with the
nonce replaced) and checks the nonce differs per request.

## Security items

| Item | What changed | Proof | Status |
| --- | --- | --- | --- |
| L1 | `next.config.ts`: `Strict-Transport-Security: max-age=31536000; includeSubDomains` added when `NODE_ENV=production`. nosniff, `Referrer-Policy: no-referrer` and `Permissions-Policy` were already there and are unchanged. | `tests/unit/security-headers.test.ts` (production has HSTS and the other three; development has no HSTS) | done |
| L2 | The `//host` link rule lived in `components/ws/Conversation.tsx`, which nothing imports any more (its only consumer `ws/Ask.tsx` was dead, D4). Deleted. The live twin `components/ask/AnswerView.tsx` builds links with `safeLink`, which already refuses `//`, backslashes and non-http(s). | `tests/unit/answer-view.test.tsx` (`//evil.example/x`, `javascript:`, `/\evil`, `data:` and empty give no link; hostile text renders inert) | done (by deletion plus a test on the live path) |
| L3 | `components/ws/EvidenceDrawer.tsx` deleted under D4. | knip, tsc | done |
| L4 | New `lib/upload-store.ts`: keys are `upload:v2:<user id>:<name>:<size>:<mtime>`. The owner is set by `WorkspaceShell` from `me.user_id` (`app/(client)/layout.tsx` passes `userId`). With no known user nothing is read or written. `clearSession()` in `lib/api.ts` (called by the sign-out button) calls `clearUploadSessions()`, which removes every `upload:v1:` and `upload:v2:` key. `lib/upload.ts` uses the store. | `tests/unit/upload.test.ts` (new test: another user sees nothing, no user sees nothing, sign-out removes v1 and v2 entries) | done. Not covered: a session that ends by expiry without pressing Sign out keeps its entries until the next sign-out, but they stay unreadable by another user (per-user key). |
| L5 | `app/v1/[[...path]]/route.ts` no longer calls `request.arrayBuffer()`. It refuses with 413 when `content-length` is over the limit, and otherwise streams the body (`duplex: "half"`) through a byte counter (`lib/proxy-body.ts`) that cuts off a body with no or a false length (413). Limits: 1 MiB for every call; 21 GiB only for `PUT /v1/documents/{id}/content` (the dev stand-in for a signed URL, streamed, the API enforces the admin's per-file limit). The `/v1/files/uploads/.../parts` 9 MiB case from the audit is gone with D1. | `tests/unit/v1-proxy.test.ts` (declared 413 forwards nothing; small body forwarded as a stream; undeclared oversize body 413; limit per route) | done |
| L6 | `__Host-ai_preauth_once` added to `AUTH_COOKIES` in the proxy. | `tests/unit/v1-proxy.test.ts` (cookie header forwarded is exactly session, preauth, preauth-once; `other` and `analytics` dropped) | done |
| L11 | The error log prints `request.nextUrl.pathname` and the error class name only. | `tests/unit/v1-proxy.test.ts` (an upstream error message containing `?token=SECRET` never reaches the log) | done |
| L15 | `npm audit fix` (without --force, also `--dry-run`) changes nothing: the only advisory (GHSA-vfj7-8cjw-p6xm, `braces`, vulnerable range `<=3.0.3`) has no fixed release (3.0.3 is the latest, `npm view braces versions`), so no override is possible. The only offered fix is `eslint-config-next@14.2.35`, a major downgrade, not applied. Before 5 high, after 5 high, all dev-only (`npm audit --omit=dev` is 0). Nothing major moved. | `npm audit`, `npm audit fix --dry-run` | accepted and recorded; revisit when `braces` publishes a fix |
| Source maps | `next.config.ts`: `productionBrowserSourceMaps: false`, explicit. | `security-headers.test.ts`; after `npm run build` `find .next/static -name '*.map'` gives 0 | done. Note: `.next/server` (317) and `.next/standalone` (56) hold server-side `.map` files; they are not served to browsers (only `/_next/static` is). Handoff below. |

## Dead code

| Item | What changed | Status |
| --- | --- | --- |
| D1w | Deleted `app/(client)/research-files/**`, `lib/research-files.ts`, `lib/file-generator.ts`, `tests/unit/research-files.test.tsx`. `grep -rn "v1/files" app components lib ui tests e2e proxy.ts` (outside `lib/gen`) finds nothing, so the regenerated client will not break the build. (`FileAnalysisRun`, `/file-analysis`, is a different live route.) | done |
| D2 | `proxy.ts`: the `unavailableOperatorRoutes` block removed; `response = NextResponse.next(...)`. CSP code untouched. Test: operator paths pass through. | done |
| D3 | `git rm` of `.probe.mjs`, `.csp.mjs`, `.gal.mjs`, `.shots.mjs`. Three of them held a hard-coded dev login (S3); they are gone, value not printed. | done |
| D4 | Deleted the listed `components/ws/*` (22 tsx plus `evidence.ts`), `ui/{ConfirmButton,DataTable,Feedback,Input,RunProgress,Scene,SignedIn,ThemeToggle,theme,theme-server}`, `lib/{client,operator-render,report,run-stop,schedule}.ts`, `lib/api/{denylist,errors}.ts`, and the three `app/operator/(console)` files. `ui/contrast.ts` is kept (used by the re-enabled token test). Also deleted, found dead only after the above: `components/ws/{Composer,Conversation,GradeMark}.tsx` (their only importers were the deleted `ws/Ask`, `ws/AccountAsk` and friends; knip confirms). `lib/ws-format.ts` `runState`/`RunState` deleted. tsc and build pass. | done, except dead exports below |
| D4b | `app/api/operator/providers/route.ts`, `lib/provider-labels.ts` deleted. | done |
| D5 | The eight redirect stubs deleted. D6 stubs and D15/D17 files untouched. | done |
| D7 | Deleted `e2e/parity/*`, `e2e/review/*`, `playwright.parity.config.ts`, `playwright.review.config.ts`, `screenshot_test.spec.ts`, `scripts/{route-js,replica-shots,visual-shots}.mjs`. Kept `scripts/{shots,phase4b-shots,copy-standalone-assets}.mjs`, `e2e/replica`, `e2e/helpers.ts`. | done |
| D8 | `tests/unit/research-files.test.tsx` and `run-stop.test.tsx` deleted. | done |
| D9 | `npm uninstall`: `@radix-ui/react-popover`, `@radix-ui/react-slot`, `@radix-ui/react-tooltip`, `@tanstack/react-table`, `clsx`, `tailwind-merge`, `postcss`, `tsx`; plus `react-markdown` and `remark-gfm`, which only the deleted `ws/Conversation.tsx` used (knip reported them unused after D4). `package.json` and `package-lock.json` updated; build passes. | done |
| D26 | `LEGACY_TO_DELETE.txt` deleted. The exclude lists in `tsconfig.json`, `eslint.config.mjs` and `vitest.config.ts` now hold only `components/ProviderKey.tsx` (D15) and `.next-parity`; vitest excludes nothing but `node_modules`. `ui/fonts` is live (`tokens.css`), kept. | done |
| Dead exports in live files | Only `runState`/`RunState` removed. Left (low value, each a one-word change, none a risk): `RunSteps` `Checkpoint` (Phase 6), `StepList`, `TERMINAL`; `operatorMethodIsSafe`; `query.ts` `LIMITS`, `needsValue`; `primitives.tsx` `cx`, `Section`, `EmptyState`, `LoadError`, `Field` (kept: the state work needs them); `design.tsx` `gradeLabel`. | partly |

## S1 and D8b: re-enabled tests

All excluded tests now run. Where the design had moved on, the test was updated to the current design; no real check was deleted.

- `ws-copy.test.ts`: `allStrings` is gone from `lib/messages.ts`, so it now lives in the test (test-only). The ternary false positives in the JSX scan are skipped like the third test does. The re-enabled lint found a real violation, `FileAnalysis.tsx` "From your files · private to your workspace" (middle dot), fixed to a comma.
- `ui-tokens.test.ts`: the app is light-only now (`tokens.css` has one `:root`), so the dark-mode pairs were replaced by the current tokens: ink, ink-2, text-3, text-nav, muted on bg and surface; every pill ink on its fill; admin text on admin navy; accent on white; white on accent. All pass at 4.5:1, nothing weakened. A test fails if a half-finished `data-theme` block is shipped.
- `documents.test.tsx`: `sourceLabel` was removed from `lib/documents`; the drop test follows the current labels ("Add files", "Ready") and the single-PUT protocol.
- `components.test.tsx` keeps `useApi` (abort of the obsolete request); the RunProgress, DataTable, Input, Meter and GradeMark tests tested deleted components.
- `ws-components.test.tsx` deleted (every subject dead). Its "model output renders inert" check lives on as `answer-view.test.tsx` against the live `AnswerView`.
- `chat-helpers.test.ts`: the citation helpers died with `ws/Conversation`; `parseFrame` now tested from `lib/sse` (including the "invalid" frame).

## S7: one API origin

New `lib/api-origin.ts`: `apiOrigin()` returns `API_ORIGIN` without a trailing slash; unset gives `http://127.0.0.1:8080` outside production and `null` in production. Used by the five files (`readyz`, `v1` proxy, `operator-oidc`, `server-api`, `auth/logout`).

Reconciliation: the two values were `127.0.0.1:8080` (dev) and `http://api:8080` (production fallback in the proxy only). I kept the first as the single development default and removed the second: a compose service name must not be a silent production fallback. In production a missing `API_ORIGIN` now fails closed (proxy 503, `/readyz` 503 "unconfigured", server pages throw `ServerApiError(503)`, logout skips the API call). The Dockerfile already sets `ENV API_ORIGIN=${API_ORIGIN}` with default `http://api:8080`, so images are unaffected. I put the function in its own module rather than `server-api.ts` because that file imports `next/headers`, which the route handlers and the OIDC unit tests should not pull in. `operatorApiOrigin()` still allows `http://api:8080` only when `API_ORIGIN` says so explicitly.

## Logic moved into web/lib (with `tests/unit/lib-moves.test.ts`)

- `lib/notifications.ts`: the sidebar note wording, links and warning flag, from `app/(client)/layout.tsx`.
- `lib/accounts-list.ts`: the news line and row order, from `app/(client)/accounts/page.tsx`.
- `lib/limits.ts`: `limitLine`, `limitReached`, `LimitKind`, from `components/Limits.tsx` (re-exported there, call sites unchanged).
- `lib/upload-store.ts` (L4), `lib/proxy-body.ts` (L5), `lib/api-origin.ts` (S7).
- Not moved (no real business logic): the small duration formatters in `RefreshForm` and `ResearchCompany` differ in wording on purpose.

No state library added; state management is unchanged.

## Loading, empty and error states per route

Added in this work: `app/error.tsx` (every route), `app/(client)/error.tsx` (inside the shell), `app/operator/(console)/error.tsx` and `loading.tsx`, `app/not-found.tsx`, `ui/RouteError.tsx` (the shared card: StateCard, a quotable reference from the digest, a Reload button, never the error text), and a failure card in `ModelPreferences` (before, a failed model list left the skeleton forever; `useModelChoicesState` added). Existing classes and components only: `StateCard`, `Skeleton`, `Loading`, `.pg`, `.state`. `global-error.tsx` (root layout crash) is not added: it replaces the whole document and cannot load the stylesheets.

"Seg" = the segment `loading.tsx`/`error.tsx` now applies: client routes get `(client)/loading.tsx` (existed) and `(client)/error.tsx` (new); auth and root get `app/error.tsx`; operator console gets its own pair.

| Route | Loading | Empty | Error |
| --- | --- | --- | --- |
| `/` Ask | seg loading; stream shows "Writing the answer" | starters / empty prompt | StateCard on `ServerApiError`; stream failure text; seg error |
| `/accounts` | seg loading | StateCard "No accounts yet" | StateCard "could not be loaded"; seg error |
| `/accounts/add` | seg loading | components handle missing estimate/library | estimate/library failure degrades to null; seg error (new) |
| `/accounts/[domain]`, `/[sheet]`, `/files`, `/refresh` | seg loading | StateCard / `SheetView` empties | StateCard on failure and on 404 "not found"; seg error (new) |
| `/research/new` | seg loading; `DocumentDrop` stages | `DocumentDrop` / estimate null | `NewResearch` start errors; seg error (new) |
| `/schedules` | seg loading | `SchedulesView` empty | StateCard; seg error (new) |
| `/mail-and-drive`, `/support-access` | seg loading | StateCard / list empties | StateCard on failure; seg error (new) |
| `/model-preferences` | skeleton | StateCard "No models are available" | StateCard (new); seg error |
| `/runs/[run_id]` | `RunPanel` steps waiting | files empty line | `RunPanel` error handling; seg error (new) |
| `/alerts /chat /downloads /runs /usage /settings/*` | redirect stubs (D6, kept) | n/a | n/a |
| `/login /forgot /reset /invite /verify-email` | client forms, button busy states | n/a | inline `Notice`; root error (new) |
| `/operator` console pages (`clients`, `clients/[tenantId]`, `defaults`, `library`, `runs`, `users`, `view`, `/`) | `ConsoleLoad` skeleton + seg `loading.tsx` (new) | "No clients yet", "No users yet", "No runs yet", "The library is empty", "No accounts yet", admins line | `ConsoleLoad` card with Try again (403 "Operators only"); seg error (new) |
| `/operator/login` | form | n/a | inline notices; root error |
| unknown URL | n/a | n/a | `not-found.tsx` (new) |

Test: `tests/unit/route-states.test.tsx` (the shared error card, reference, no error text, reset; model preferences skeleton then error card).

## Handoffs

- handoff: `web/Dockerfile`, after the standalone copy: delete server-side source maps from the image, for example `find .next/standalone -name '*.map' -delete` (56 files; not served, but not needed). Agent 5.
- handoff: after merging, the lead regenerates `web/lib/gen/api.gen.ts`; no live web code references `/v1/files*`.
- handoff (lead, docs): `docs/WIRING.md` lists calls from deleted files; `docs/build/VISUAL_NOTES.md` cites `scripts/visual-shots.mjs`; `docs/SCREEN_MATRIX.md`, `docs/UI_BACKEND_AUDIT.md` mention the parity suite; `docs/UI_REMOVED.md`, `docs/frontend/operator-route-map.md` say the four operator routes are "hidden in production" (they are plain 404 now); D24 `docs/design-review/` can go.
- handoff (Agent 4, FYI): `PUT /v1/documents/{id}/content` is proxied with a 21 GiB outer bound and streamed; the API's per-file limit still decides.

## Not done or skipped

- L15: no fix exists (see above); accepted.
- Dead exports in live files: mostly left (listed above).
- No browser run: no signed-in check on port 3026 (API origin check would 403); proof is tsc, eslint, vitest, build. The lead's Playwright and axe pass covers the new error/loading/not-found screens and the model-preferences card.
