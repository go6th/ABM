# Frontend audit: `web/` (Next.js app of "Account Intelligence by 6th")

Audited read-only on 2026-11-03, branch `feat/final-product`. Everything below was read from code in `/home/sakib/ABM Generator/web` unless marked **unverified**. Nothing was run (no build, test, lint or browser), so "passes/fails" claims are static reading only.

Conventions: "tenant" = the client workspace plane (`/v1/*`); "operator" = 6th staff plane (`/operator/v1/*`). "Stub" = a page file that only calls `redirect()`.

---------------------------------------------------------------------------------------------------

## 1. Tech stack, dependencies, tooling, build

### 1.1 Stack at a glance

| Item | Value | Evidence |
|---|---|---|
| Framework | Next.js **16.4.1**, **App Router** (`web/app/`), no `pages/` dir | `package.json`, `node_modules/next/package.json` |
| UI library | React / React DOM **19.3.0** | `package.json` |
| Language | TypeScript **6.0.3**, `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride`, target ES2022, path alias `@/*` -> `web/*` | `tsconfig.json` |
| Styling | **Plain hand-written CSS** (6 files, 1,507 lines) driven by CSS custom properties. **No Tailwind, no CSS-in-JS, no CSS modules** | `ui/*.css`, no tailwind in `package.json` |
| Data validation | **zod 4.6.7**: every request and response is parsed against schemas generated from the API's OpenAPI | `lib/gen/api.gen.ts` |
| Lint | ESLint 9 flat config: `eslint-config-next` core-web-vitals + typescript; extra rules `react/no-danger`, `no-eval`, `no-implied-eval` = error | `eslint.config.mjs` |
| Unit tests | **Vitest 5.0.3** (threads pool), `@testing-library/react`, `jsdom` (per-file `// @vitest-environment jsdom` in 30 of 50 files) | `vitest.config.mts`, `tests/unit/` |
| E2E tests | **Playwright 1.63** + `@axe-core/playwright` (two configs) | `playwright.config.ts`, `playwright.replica.config.ts`, `e2e/` |
| Next "middleware" | Next 16 renamed it: `web/proxy.ts` (exports `proxy`, sets CSP nonce only; **does no auth**) | `proxy.ts` |
| Node (runtime image) | `node:22-slim` pinned by digest; no `engines` field in `package.json` | `Dockerfile` |
| State management | None (no Redux/Zustand/SWR/React Query). Server Components fetch; client components use `useState`/`useEffect`, one tiny `useApi` hook, and two React contexts | see section 3 |

The repo's own `web/AGENTS.md` warns that this Next version has breaking changes and tells agents to read `node_modules/next/dist/docs/` first. `proxy.ts` cites `node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md`.

### 1.2 `package.json` dependencies (name, version, use)

Scripts: `dev` (`next dev -p 3000`), `build`, `postbuild` (`node scripts/copy-standalone-assets.mjs`), `start`, `typecheck` (`tsc --noEmit`), `lint` (`eslint .`), `test` (`vitest run`), `e2e` (`playwright test`).

| Package | Declared / installed | Used for in this app (checked by grep of imports) |
|---|---|---|
| `next` | `16.4.1` (pinned) | Framework: App Router, Server Components, route handlers, `next/headers`, `next/navigation`, `proxy.ts` |
| `react`, `react-dom` | `^19.3.0` / 19.3.0 | UI |
| `zod` | `4.6.7` (pinned) | Only imported by generated `lib/gen/api.gen.ts` plus a few direct uses of the generated schemas (`OperatorSession`, `ResearchLive`) |
| `react-markdown` | `10.1.0` (pinned) | `components/ask/AnswerMarkdown.tsx`: renders the Markdown the model writes in an Ask answer |
| `remark-gfm` | `4.0.1` (pinned) | Same file: tables, strikethrough, task lists in answers |
| `rehype-sanitize` | `6.0.0` (pinned) | Same file: sanitises the Markdown HTML tree (the app bans raw HTML injection) |
| `@radix-ui/react-dialog` | `^1.2.0` | **Only 2 files**, both operator console: `operator/(console)/library/page.tsx` (publish / unpublish dialogs) and `clients/page.tsx` ("Add client" dialog). All other dialogs (e.g. `DeleteAccount`) are hand-made |
| `lucide-react` | `^1.52.0` (1.52.0) | **One icon** (`ChevronRight`) in `operator/(console)/clients/page.tsx`; every other icon is inline SVG in `ui/design.tsx` / `ui/AuthFrame.tsx`. Candidate for removal |
| `typescript` | `^6.0.3` | Type checking |
| `eslint`, `eslint-config-next` | `^9.39.5`, `^16.4.0` | Lint |
| `vitest`, `@vitest/coverage-v8` | `^5.0.3`, `5.0.3` | Tests. **Coverage package is installed but no script or config uses it** (unverified that CI collects coverage) |
| `@testing-library/react`, `jsdom` | `^16.3.3`, `^30.1.2` | Component/hook tests |
| `@playwright/test`, `@axe-core/playwright` | `^1.63.0`, `^4.13.0` | E2E and accessibility gate |
| `@types/node`, `@types/react`, `@types/react-dom` | `^26.6.4`, `^19.3.0` | Types |

Only 7 runtime dependencies, 3 of them for one feature (Markdown answers). `package-lock.json` is committed (365 KB, ~696 `node_modules/` entries). Several runtime packages are pinned exactly, others use caret ranges (inconsistent but harmless with the lock file).

### 1.3 Build and runtime config

| File | What it does |
|---|---|
| `next.config.ts` | `output: "standalone"`; `distDir` from `NEXT_DIST_DIR` (lets a second dev server build elsewhere); `pageExtensions` adds `dev.tsx` only when not production (comment says this serves a `/__design` gallery, but **no `*.dev.tsx` file exists** and no `__design` route exists: dead config); `reactStrictMode`; `poweredByHeader:false`; `productionBrowserSourceMaps:false`. Headers on every path: HSTS (production only), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Cross-Origin-Opener-Policy: same-origin`. **There is no `rewrites()`**; see 5.1 |
| `proxy.ts` | Per request: random nonce; CSP `default-src 'self'; script-src 'self' 'nonce-X' 'strict-dynamic' (+'unsafe-eval' only in dev); style-src 'self' 'nonce-X'; img-src 'self' blob:; font-src 'self'; connect-src 'self' + UPLOAD_ORIGIN; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`. Sets `x-nonce` request header. Matcher excludes `/v1/`, `_next/static`, `_next/image`, `favicon.ico` (so the API proxy responses do not get a page CSP; `/api/*`, `/healthz` etc. do) |
| `app/layout.tsx` | `export const dynamic = "force-dynamic"` at the ROOT: every page is rendered per request (needed so the nonce can be applied). No static pages / no caching. Imports the 6 CSS files globally. `themeColor: "#F5F6F8"` (slightly different from `--bg: #F7F8FD`) |
| `Dockerfile` | 3-stage: `deps` (`npm ci --ignore-scripts`), `build` (`ARG API_ORIGIN=http://api:8080` -> ENV, `npm run build`, deletes every `*.map`), `runtime` (digest-pinned `node:22-slim`, removes npm/npx/corepack, user `web` uid 10001, copies `.next/standalone` + `.next/static`, `EXPOSE 8080`, `HEALTHCHECK` on `/healthz`, `CMD node server.js`). Port comes from `$PORT` (Cloud Run 8080); `infra/stack/main.tf` sets web `port = 3000` (**unverified** whether that is overridden by PORT at deploy) |
| `.dockerignore` | node_modules, .next, coverage, test results, `.env`, `.env.*`, logs |
| `scripts/copy-standalone-assets.mjs` | postbuild: copies `.next/static` (and `public/` if present) into `.next/standalone`. `public/` is **empty/absent**, so there is no favicon.ico, robots.txt or manifest. Only `app/icon.svg` exists |
| `.env.example` | `API_ORIGIN`, `OPERATOR_GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI`, `STAFF_WORKSPACE_DOMAIN`. **Missing** `UPLOAD_ORIGIN` and `STAFF_EXTERNAL_EMAILS`, which the code reads (see 5.4) |
| `web/.env.local` | Exists locally (gitignored, mode 600), contains the 5 keys above. Values deliberately not read into this report |
| CI (`.github/workflows/ci.yml`, job `web`) | `npm ci`, `typecheck`, `lint`, `test`, `build` in `web/`; a later job builds the app and installs a browser for e2e. Make targets `make e2e`, `make replica-up` exist |

---------------------------------------------------------------------------------------------------

## 2. Every page and route

### 2.1 Totals

* **52 `page.tsx` files** under `web/app`.
* **18** of them are redirect-only stubs ("Removed from the product. This route redirects until the file is deleted"): 14 tenant + 4 operator.
* **34 real screens**: 5 sign-in/account flows, 2 legal, 17 tenant workspace, 1 operator sign-in, 9 operator console.
* **8 route handlers** (`route.ts`), **2 passthrough layouts** (`settings/layout.tsx`, `usage/layout.tsx`) kept only for removed folders.

### 2.2 Route groups and layouts

| Layout / boundary | File | Behaviour |
|---|---|---|
| Root | `app/layout.tsx` | `<html lang="en"><body>`, global CSS, force-dynamic, metadata title "Account Intelligence by 6th" |
| Root error / 404 | `app/error.tsx`, `app/not-found.tsx` | `RouteError` card; "Page not found" with a link to Ask. **No `global-error.tsx`** |
| `(auth)` | `app/(auth)/layout.tsx` | Pure passthrough. Pages draw `AuthFrame` themselves |
| `(client)` | `app/(client)/layout.tsx` | Server Component. Runs 4 parallel reads: `GET /v1/me`, `/v1/chat/threads`, `/v1/notifications`, `/v1/models/choices` (`Promise.allSettled`). `/v1/me` failure throws to the error page; a 401 (via `serverGet`) redirects to `/login?next=`; `me` null redirects to `/login`. Renders `WorkspaceShell` (sidebar) wrapping `ModelChoicesProvider`. **This layout is the real auth gate for every tenant screen** (there is no middleware auth) |
| `(client)` loading / error | `loading.tsx` (skeleton), `error.tsx` | |
| `accounts/[domain]` | `layout.tsx` | Passthrough (each account screen draws its own header) |
| `(legal)` | `LegalPage.tsx` | Frame for /privacy and /terms; public |
| `operator/(auth)` | `layout.tsx` | Passthrough for the staff sign-in |
| `operator/(console)` | `layout.tsx` -> `OperatorSessionGate` -> `AdminShell` | **Client-side gate**: on every pathname change it fetches `/api/operator/session`; 401 -> `router.replace('/operator/login?return_to=...')`; 503 -> "Staff sign in is not configured". The HTML shell of console pages is served without a check; only data calls need the cookie. Also `loading.tsx`, `error.tsx` |

### 2.3 Public pages (no session needed)

| URL | File | Screen | Components | Backend calls | Auth |
|---|---|---|---|---|---|
| `/login` | `(auth)/login/page.tsx` (client) | Workspace sign-in: email + password + "keep me signed in"; "Continue with SSO"; workspace picker when one email maps to several workspaces; "resend verification" | `AuthFrame`, `AuthPitch`, `KeyIcon` (`ui/AuthFrame.tsx`) | `POST /v1/auth/login`, `POST /v1/sso/discover` then browser navigates to `/v1/sso/<slug>/start...`, `POST /v1/auth/email/resend` (all via `apiPublic`, no CSRF) | None. `?next=` honoured only if it is a same-site path not starting `//`, `/\`, `/login` |
| `/forgot` | `(auth)/forgot/page.tsx` | Request a password reset email | `AuthFrame` | `POST /v1/auth/password/reset/request` | None |
| `/reset` | `(auth)/reset/page.tsx` | Set a new password from emailed token | `AuthFrame` | `POST /v1/auth/password/reset` | Token in URL |
| `/invite` | `(auth)/invite/page.tsx` | Accept an invite and choose a password | `AuthFrame` | `POST /v1/auth/invite/accept` | Token in URL |
| `/verify-email` | `(auth)/verify-email/page.tsx` | Confirms an email token on load | `AuthFrame` | `POST /v1/auth/email/verify` | Token in URL |
| `/privacy` | `(legal)/privacy/page.tsx` | Privacy policy (static text) | `LegalPage` | none | None |
| `/terms` | `(legal)/terms/page.tsx` | Terms of service (static text) | `LegalPage` | none | None |
| `/operator/login` | `operator/(auth)/login/page.tsx` (server) | Staff sign-in: "Sign in with Google" link to `/api/operator/login`; shows error codes; says "not configured" if the OIDC env is missing | `AuthFrame plane="operator"`, `AuthPitch`, `GoogleMark` | none (reads env via `googleOperatorOidcConfig()`) | None (staff must pass Google + API checks) |

### 2.4 Tenant workspace pages (`(client)` group; all need a session, enforced by the layout)

All are Server Components with `dynamic = "force-dynamic"` unless noted, reading through `serverGet` (server-to-server, cookie forwarded, 10 s timeout, Zod-validated). Failure shows a `StateCard` ("could not be loaded, reload") instead of the route error page.

| URL | File | What the screen shows | Main components | Backend endpoints | Role needs |
|---|---|---|---|---|---|
| `/` | `(client)/page.tsx` | **Ask** (home): "What do you want to know?", composer with account scope, model/effort chips, search toggle, "+" for files/Drive; streamed answers with numbered citations; token meter. Query params: `?thread=` open chat, `?account=` preselect (by domain), `?q=` auto-ask once | `Ask`, `AskComposer`, `AnswerView` (`AnswerMarkdown`, `AnswerStructured`), `ContextMeter`, `ModelMenu`, `Limits` | SSR: `GET /v1/accounts?limit=200`, `/v1/chat/threads/{id}`, `/v1/me`. Browser: **`POST /v1/chat/ask` (SSE)**, `GET /v1/me/limits`, `GET /v1/models` | Any signed-in role |
| `/chats` | `chats/page.tsx` | All chat threads, Open / Delete | `ChatList` | `GET /v1/chat/threads`; `DELETE /v1/chat/threads/{id}` | Any |
| `/accounts` | `accounts/page.tsx` | List of accounts: initial tile, news pill, lanes, last researched, refresh cadence; "Add account" | `Pill`, `StateCard`; helper `lib/accounts-list.ts` | `GET /v1/accounts?limit=200` | Any |
| `/accounts/add` | `accounts/add/page.tsx` | Add an account 3 ways: research a new company, add ready-made "6th Library" accounts, analyze own files | `AddAccount`, `ResearchCompany`, `UsageProceed` | SSR `GET /v1/research/estimate`, `/v1/library/available`; `POST /v1/accounts/from-library`, `POST /v1/research`, `/v1/limits/requests` | Any (limits enforced by API) |
| `/accounts/[domain]` | `accounts/[domain]/page.tsx` | **Account workspace**: header, KPI tiles (Signals/People/Jobs/News), progress or "Resume" or "Research finished" gaps, "The one minute version" with citation links, Key findings or "What changed", account-scoped Ask box, delivered-files tabs, right rail "Deep dive" (11 links with counts), Files card, uploaded files | `FilesCard`, `UploadedFilesCard`, `LiveSync`, `AccountAsk`, `ResumeResearch`, `ResearchProgress`, `DeleteAccount`, `ParityWorkspace` (in a Suspense), `Grade`, `Pill` | SSR `GET /v1/accounts/{d}/workspace`, `/v1/accounts/{d}/parity/screen` (404 ok), `/v1/documents?account_id=`; client: `GET /v1/documents?account_id=` (LiveSync), `POST /v1/research/{id}/stop`, `POST /v1/research/resume`, `DELETE /v1/accounts/{d}`, `GET/POST download-everything`, `/v1/downloads/{job}`, deliverables download, `/v1/accounts/{d}/parity/...` | Any; viewers cannot list documents (the card is omitted) |
| `/accounts/[domain]/[sheet]` | `accounts/[domain]/[sheet]/page.tsx` | **Deep-dive table**; `sheet` is one of `people, signals, jobs, technology, news, mail-and-drive, sources, searches, facts, coverage`. Server-side sort/filter/paging via `?filter ?sort ?dir ?limit ?offset`; `?tab=` shows another sheet of the delivered workbook; CSV and Excel download; "Private to you" pill | `SheetView`, `SheetTabs`, `TabView`, `ParityTable` | `GET /v1/accounts/{d}/sheets/{sheet}`, `.../sheets/{sheet}/tabs/{tab}`, `.../sheets/{sheet}/csv`, `/xlsx`, cell-text expand, `.../deliverables/download` | Any |
| `/accounts/[domain]/archive` | `.../archive/page.tsx` | Web-archive style view. With `?url=` the stored copy of a page/job posting; without, an index of everything archived with filters (`title publisher published source_type grade event_type person lane collected kind`) and "Show more" | `ArchivedCopyView`, `ArchiveIndexView` (`ArchiveView.tsx`) | `GET /v1/accounts/{d}/archive?url=`, `/archive/list?...` | Any |
| `/accounts/[domain]/files` | `.../files/page.tsx` | "Analysis of your file": findings from uploaded files, gaps, "Also check public sources", download internal summary | `FileAnalysis` | `GET /v1/accounts/{d}/file-analysis[?run=]`; client `GET /v1/runs/{id}/internal`, `/v1/runs/{id}/internal/file`, documents | Any |
| `/accounts/[domain]/refresh` | `.../refresh/page.tsx` | "Refresh X from the web?": scopes (jobs / people / full / topic) with token+$ estimate, model chips, confirm | `RefreshForm`, `UsageProceed`, `ModelMenu` | SSR `GET /v1/accounts/{d}/refresh`; `POST /v1/accounts/{d}/refresh` | Any; "turned off" state if admin disabled web refresh |
| `/research` | `research/page.tsx` | **Research board**: every running/recent research, Start and Stop, selected research detail, token card; live | `ResearchBoard`, `TokenMeter`, `UsageProceed` | SSR `GET /v1/research/live[?selected]`, `/v1/research/estimate`; browser **SSE `GET /v1/research/live/stream`** with polling fallback; `POST /v1/research/{id}/stop`, `POST /v1/research` | Any |
| `/research/new` | `research/new/page.tsx` | "New research" with tabs: Company name, Upload a file (`?tab=file`, `?source=drive`) | `NewResearch`, `ResearchCompany`, `DocumentDrop`, `DriveChooser` | SSR `GET /v1/research/estimate`; `POST /v1/documents/uploads` (+ direct PUTs to object storage), `/documents/{id}/complete`, `/status`, `/confirm-company`, `/from-drive`, `/parts/{n}/retry`, `GET /v1/documents/drive-files`, `POST /v1/runs`, `GET /v1/runs/{id}` | Any |
| `/runs/[run_id]` | `runs/[run_id]/page.tsx` (**client component**) | One run's steps, files as they appear, gate verdict, coverage table, Stop/Refresh, cap approval card. Only reachable via a "details" link in `ResearchProgress` | `RunPanel`, `RunFiles`, `useRun` (`components/ws/RunSteps.tsx`), `PageHead`/`Status` from `ui/primitives.tsx` | `GET /v1/runs/{id}`, `/progress`, **SSE `/v1/runs/{id}/events`**, `/cap`, `/spend/live`, `POST /v1/runs/{id}/cap/approve`, `.../stop`, `GET /v1/exports?domain=` | Any. Uses the OLD design vocabulary (`.page`, `.table-wrap`), unlike the rest (`.pg`) |
| `/schedules` | `schedules/page.tsx` | Per-account refresh cadence (3 days / Weekly / 2 weeks / Monthly / Off), monthly estimate | `SchedulesView` | SSR `GET /v1/schedules`; `PUT /v1/schedules/{accountId}` | Any |
| `/mail-and-drive` | `mail-and-drive/page.tsx` | User's own read-only Gmail/Drive/Outlook/OneDrive/SharePoint connections; `?error=` banner codes | `MailDrive` | SSR `GET /v1/mail-and-drive`; `POST /v1/mail-and-drive/{kind}/connect|disconnect|sync` | Any; page says "turned off" when admin disabled |
| `/model-preferences` | `model-preferences/page.tsx` (static server comp) | Default research and analysis model + effort, saved on profile | `ModelPreferences`, `ModelMenu` | `GET /v1/models/choices` (via layout context), `PUT /v1/me/model-preferences` | Any |
| `/team` | `team/page.tsx` | Teammates: invite by email+role, members list; people-data consent card | `Teammates`, `PeopleConsentCard` | SSR `GET /v1/users/team`, `/v1/settings/people-consent`; `POST /v1/users/invite`, `POST /v1/settings/people-consent` | **Admin** (sidebar link only for `role==='admin'`; page itself shows "Only your administrator..." on a 403, so it is API-enforced) |
| `/support-access` | `support-access/page.tsx` | Give a named 6th staffer time-boxed read-only access, list grants, support-view log, revoke | `SupportAccess` | SSR `GET /v1/support/grants`, `/v1/support/events`; `POST /v1/support/grants`, `/grants/{id}/revoke` | **Admin**; needs recent MFA for creating a grant (server says so) |

### 2.5 Removed-but-still-present tenant routes (14 stubs + 2 layouts)

All stub pages are `redirect("/")`:

`/alerts`, `/chat`, `/downloads`, `/runs`, `/usage`, `/settings`, `/settings/alerts`, `/settings/connectors`, `/settings/crm`, `/settings/model-key`, `/settings/security`, `/settings/sso`, `/settings/support`, `/settings/users`. Plus passthrough `settings/layout.tsx` and `usage/layout.tsx`. These can be deleted (the file comments say so). Note `/audit` has **no** file at all (it 404s) but `e2e/tenant.spec.ts` expects it to redirect (see 6).

### 2.6 Operator console pages (`/operator/*`, staff only)

| URL | File | Screen | Backend (all through `/api/operator/v1/*` BFF) |
|---|---|---|---|
| `/operator` | `(console)/page.tsx` | Overview (30 days): isolation check pill, KPIs, questions per week, categories, idle clients, plus `RequestsList` | `GET /operator/v1/console/overview`, `.../console/requests`, `POST .../requests/{tenant}/{id}/approve|decline` |
| `/operator/clients` | `clients/page.tsx` | Client list; "Add client" dialog (shows invite link to copy) | `GET/POST /operator/v1/console/clients` |
| `/operator/clients/[tenantId]` | `clients/[tenantId]/page.tsx` | Client detail: limits, features, library accounts, invite, suspend/reactivate | `GET/PUT .../console/clients/{tenant_id}`, `POST .../invite`, `POST .../users`, `POST /operator/v1/tenants/{id}/suspend|reactivate` |
| `/operator/users` | `users/page.tsx` | All users with usage counts, change limits, terminate / re-enable | `GET .../console/users`, `PUT .../users/{tenant}/limits`, `POST .../terminate`, `.../re-enable` |
| `/operator/library` | `library/page.tsx` | "6th Library": upload a finished account zip, see the client-ready check result, publish/unpublish to chosen clients (Radix dialogs) | `GET /operator/v1/library`, `POST .../library/uploads` (binary, up to 100 MB), `POST .../library/{id}/publish|unpublish`, `GET .../console/clients` |
| `/operator/runs` | `runs/page.tsx` | Credits by purpose + every run across clients | `GET .../console/runs` |
| `/operator/runs/[tenantId]/[runId]` | `runs/[tenantId]/[runId]/page.tsx` | One run's token card (same as client sees) | `GET .../console/runs/{tenant_id}/{run_id}` |
| `/operator/defaults` | `defaults/page.tsx` | Platform defaults (caps, idle expiry, models, safeguards, admins) | `GET/PUT .../console/defaults` |
| `/operator/view` | `view/page.tsx` | "View as a client": paste a support-grant UUID, see that workspace read-only (accounts and counts) | `GET /operator/v1/view/workspace` with header `X-Support-Grant` |
| `/operator/cost`, `/spend`, `/support`, `/tenants` | stubs | `redirect("/operator")` | none |

Roles: the shell labels `viewer.role === "operator"` as "Admin", otherwise "Support". Which pages a "support" role may open is decided by the API (the UI does not hide anything per role: **unverified** what a support user sees on pages like Defaults).

### 2.7 Route handlers (`route.ts`)

| Path | File | Method(s) | Purpose |
|---|---|---|---|
| `/v1/[[...path]]` | `app/v1/[[...path]]/route.ts` | GET POST PUT PATCH DELETE HEAD OPTIONS | **The tenant API proxy** (BFF). See 5.1 |
| `/api/auth/logout` | `app/api/auth/logout/route.ts` | POST | Same-origin check; reads session (`/v1/sso/session`) to get the CSRF token, `POST /v1/sso/logout` upstream, then expires `__Host-ai_session` and `__Host-ai_preauth` (not `__Host-ai_preauth_once`) |
| `/api/operator/login` | `.../login/route.ts` | GET | Starts Google OIDC: state, nonce, PKCE S256 verifier in 10-min httpOnly cookies, redirect to `accounts.google.com`; `hd` hint unless `STAFF_EXTERNAL_EMAILS` set; canonicalises to the configured redirect origin |
| `/api/operator/callback` | `.../callback/route.ts` | GET | Verifies state (timing-safe), `iss`, exchanges code at `oauth2.googleapis.com/token` using the client secret, checks nonce, calls API `GET /operator/v1/session` with the ID token as Bearer, requires `mfa_fresh`, stores the **Google ID token itself** as the session cookie (`httpOnly`, `SameSite=Strict`, `expires` = token `exp`), 303 to the saved `return_to` |
| `/api/operator/session` | `.../session/route.ts` | GET / POST / DELETE | GET: verifies cookie against API, 401 and cookie delete if invalid or not MFA-fresh. POST: accepts `{access_token}` JSON and sets the cookie after API verification (alternate path, used by tests/dev: **unverified** who calls it in prod). DELETE: sign out |
| `/api/operator/v1/[...path]` | `.../v1/[...path]/route.ts` | GET POST PUT PATCH DELETE | Operator BFF: only paths that are `plane==="operator"` operations in the generated catalogue; adds `Authorization: Bearer <cookie>`; allows headers accept, content-type, idempotency-key, x-support-grant; JSON only (or octet-stream/zip for the library upload); 1 MB / 100 MB caps; maps upstream 502/503 to 503 |
| `/healthz` | `app/healthz/route.ts` | GET | `{status:"ok"}` liveness (Docker HEALTHCHECK, Cloud Run probes) |
| `/readyz` | `app/readyz/route.ts` | GET | Calls API `/readyz` (2 s); 503 when the API is unreachable or `API_ORIGIN` unset |

---------------------------------------------------------------------------------------------------

## 3. Components, libraries, data flow

### 3.1 Directory inventory (16,634 lines in `components`, `lib`, `ui`; of which `lib/gen` 4,037 and `lib/parity/tableSpecs.ts` 1,475 are generated)

| Dir | Contents |
|---|---|
| `components/` (root) | `Limits` (per-user "n of m researches left" + request-more), `ModelMenu` (model + effort chips; `useModelChoices*`, `useResearchModels`), `ModelPreferences`, `UsageProceed` (token/$ estimate with Proceed/Cancel) |
| `components/ask/` | `Ask` (stream state machine, 343 lines), `AskComposer`, `AnswerView`, `AnswerMarkdown` (react-markdown + gfm + sanitize), `AnswerStructured` (renders the model's `render_answer` tables/callouts), `ChatList`, `ContextMeter` (claude.ai-style ring) |
| `components/accounts/` (24 files) | Account screen pieces: `AddAccount`, `ResearchCompany`, `NewResearch`, `RefreshForm`, `ResearchProgress`, `ResumeResearch`, `SchedulesView`, `SheetView`, `SheetTabs`, `TabView`, `ParityWorkspace`, `ParityTable`, `ArchiveView` (491 lines), `DownloadEverything`, `FilesCard`, `UploadedFilesCard`, `WhatWasRead`, `LiveSync` (re-draws the page while files are read), `DeleteAccount`, `DeleteFileButton`, `MailDrive`, `SupportAccess`, `Teammates`, `PeopleConsent`, `AccountAsk`, `cell-fill.ts` |
| `components/research/` | `ResearchBoard` (333 lines) |
| `components/ws/` | `WorkspaceShell` (sidebar/topbar/mobile menu), `RunSteps` (`useRun`, `RunPanel`, `RunFiles`), `TokenMeter`, `DocumentDrop`, `DriveChooser`, `FileAnalysis` |
| `ui/` | `tokens.css`, `base.css`, `components.css` (850 lines), `shell.css`, `ask.css`, `auth.css`; `design.tsx` (Logo, NavIcon, Pill, Grade, Bar, Skeleton, StateCard), `primitives.tsx` (older: PageHead, Section, Status, Notice, EmptyState, Loading, LoadError, Field), `AuthFrame.tsx`, `RouteError.tsx`, `contrast.ts` (test-only helper), `fonts/` (Hanken Grotesk woff2 x4 + licence) |
| `app/operator/(console)/` | `AdminShell`, `OperatorNav`, `OperatorSessionGate`, `OperatorSignOut`, `RequestsList`, `ConsoleLoad`, `useOperatorApi` |

Two parallel UI vocabularies coexist: `ui/design.tsx` + `.pg/.head/.card` classes (newer, used by nearly everything) and `ui/primitives.tsx` + `.page/.page-head/.section/.rows/.table-wrap` (older; still used by `/runs/[run_id]`, `RunSteps`, `FileAnalysis`, `DocumentDrop`, `DriveChooser`, `UploadedFilesCard`, operator gate).

### 3.2 `web/lib` (non-generated)

| File | Role |
|---|---|
| `api.ts` | **The browser's single door to the API.** `api()`, `apiPublic()`, `apiStream()`, `session()`, `ApiError`, `newIdempotencyKey()`, `clearSession()`. Details in 3.3 |
| `server-api.ts` | **Server Components' door**: `serverGet<T>(path, returnTo, emptyOn)`. GET only, cookies from `next/headers` filtered to the 3 auth cookies, `x-request-id`, 10 s timeout, `redirect:"error"`, 401 -> `redirect('/login?next=')`, 404 and `emptyOn` statuses -> `null`, other errors -> `ServerApiError(status, ref)`; Zod `safeParse` of the generated response schema (mismatch -> 502) |
| `api-origin.ts` | `apiOrigin()`: `API_ORIGIN` env; in production no fallback (fail closed -> 503), in dev `http://127.0.0.1:8080` |
| `tenant-cookies.ts` | Allow-list of cookies forwarded upstream: `__Host-ai_session`, `__Host-ai_preauth`, `__Host-ai_preauth_once` |
| `same-origin.ts` | Origin vs Host/X-Forwarded-* comparison (used by every write route handler); `publicOrigin()` |
| `proxy-body.ts` | Body caps (JSON 1 MiB; local upload stand-in 21 GiB), timeouts (JSON 120 s; transfers 60 min), streaming `capBody` |
| `sse.ts` | `readFrames` parser for the **Ask** stream over `fetch` + `ReadableStream` (not EventSource, because it is a POST); `addPieces` merges `text` frames into paragraph parts |
| `useResearchLive.ts` | `EventSource('/v1/research/live/stream')`; falls back to polling `GET /v1/research/live` every 2 s on failure, retries the stream with backoff 5 -> 30 s; every event Zod-validated |
| `useRunStream.ts` | Run page: polls run record every 5 s + `EventSource('/v1/runs/{id}/events')` (progress), poll fallback 5 s, stream retry 10 s |
| `useApi.ts` | Generic `GET` hook (abort on change, stale-guard, `reload()`) |
| `upload.ts`, `upload-store.ts` | **Direct-to-object-storage resumable upload** via `XMLHttpRequest` PUT with `Content-Range`; 5 tries per chunk, backoff to 15 s; asks storage for committed bytes after a drop. Session URLs kept in `localStorage` per user id (`upload:v2:<user>:<name>:<size>:<mtime>`), wiped on sign-out. Falls back to a single PUT through `/v1/documents/{id}/content` for the dev object store |
| `documents.ts`, `limits.ts`, `usage.ts`, `chat-usage.ts`, `accounts-list.ts`, `notifications.ts`, `ws-format.ts`, `console.ts` | Pure formatters/helpers with unit tests |
| `render.ts` | Safe text/link helpers: `cellText`, `safeLink` (http/https only, no credentials), `LINK_REL`, `ownHref` (rewrites archived-copy links to the site's own `/accounts/{d}/archive`) |
| `messages.ts` | "Every user-facing string" catalogue (346 lines) but only **11 files** import it; most UI text is inline in components, so the claim is no longer true |
| `model-choices.tsx` | React context holding `ModelChoices` loaded once by the layout |
| `operator-api.ts` | `operatorRequest(key, {params, query, body, file})`: typed client for the generated **operator** operations; fetches `/api/operator/v1/...` and Zod-parses replies |
| `operator-oidc.ts` | Reads Google OIDC settings from env or `NAME_FILE`; validates URLs; cookie names (`__Host-6th_operator_*` when HTTPS) |
| `operator-proxy.ts` | Marks the one binary operation (`POST /operator/v1/library/uploads`, 100 MB) |
| `query.ts` | Table query-spec builder (filters/sorts for `GET /v1/accounts/{d}/tables/{t}`). **Not imported by any production code**, only by a test: dead (see section 7) |
| `use-is-client.ts` | Hydration-safe "am I on the client" flag |
| `parity/` | `clean.ts`, `counts.ts` (mirror of Python `parity.counts`), `tableSpecs.ts` (GENERATED from `src/parity/tables.py`) for rendering delivered-file tables identically to the Word/Excel files |
| `gen/api.gen.ts` | GENERATED by `scripts/gen_web_client.py` from `docs/api/openapi.json`: 3,913 lines of Zod schemas + an `operations` table (key = "METHOD /path", `plane: tenant|operator`, request/response schema). About 170 `/v1/...` and 49 `/operator/...` operation keys by my count; `docs/WIRING.md` says 221 backend operations (the 2-operation difference is **unverified**, probably non-/v1 routes) |
| `gen/labels.gen.ts` | GENERATED labels: run statuses, coverage, grades, lanes, alert rules, output denylist |

### 3.3 API client behaviour (`lib/api.ts`)

* Looks the call up in the generated `operations` catalogue by `(method, path template)`. Unknown operation -> `ApiError(500, "operation_not_in_contract")` before any network call. Only `plane === "tenant"` operations are allowed.
* Request body parsed with the operation's Zod schema (mismatch -> `request_contract_mismatch`); response parsed (mismatch -> 502 `contract_mismatch`). Generated schemas are `.strict()`, so **an extra field in an API reply breaks the screen**: web and API must deploy in lockstep (risk, see 7).
* Safe methods send no token. Unsafe methods send `X-CSRF-Token` from a module-level variable filled by `GET /v1/sso/session`; on a 403 with problem type `csrf_refused` it re-reads the session and replays once.
* Errors are RFC 7807 problem+json (`type` stripped of `urn:6th:problem:`), surfaced as `ApiError(status,type,detail,context,body)`.
* `credentials: "same-origin"`, `cache: "no-store"`; no token in URLs/localStorage.
* Idempotency keys: components generate `request_id`s (`useRequestKey`, `newIdempotencyKey`) and put them in request bodies for research/refresh/download.

### 3.4 Data fetching and live-update patterns

| Pattern | Where | Notes |
|---|---|---|
| Server Component `serverGet` (SSR, no client cache) | every page under `(client)` | `Promise.all/allSettled` to parallelise; `force-dynamic` |
| Client `api()` after user action then `router.refresh()` | most forms | Re-runs the server reads |
| `useApi` GET hook | `FileAnalysis`, `DriveChooser`, `UploadedFilesCard`, `WhatWasRead`, `ParityTable` | |
| **SSE via `EventSource`** | `/v1/research/live/stream` (Research board), `/v1/runs/{id}/events` (run page) | Server ends streams on a duration cap; browser auto-reconnects; polling fallback |
| **SSE via `fetch` stream (POST)** | `POST /v1/chat/ask` (Ask) | Frames: `thread`, `text`, `reset`, `answer`, `done`, `error`; thread id written with `history.replaceState` then `router.refresh()` |
| Polling with `router.refresh()` | `LiveSync` (account page while files are read, 15 s tail), `ResumeResearch` (interval), `FileAnalysis` (5 s) | |
| Timers | `TokenMeter` 15 s tick, `DownloadEverything` job polling, `ResearchBoard` clock | |
| Cross-component event | `ask:new-chat` window event from the sidebar "New chat" button to `Ask` | |

State management is local component state plus two contexts (`ModelChoicesProvider`, `OperatorViewerContext`). Only `localStorage` use: upload sessions (`lib/upload-store.ts`). No `sessionStorage`, no `document.cookie`.

### 3.5 Auth and session handling (browser side)

* Session is an httpOnly cookie `__Host-ai_session` set by the API through the proxy; JS never sees it. `Session` includes role (`admin|analyst|seller|viewer`), `step_up_fresh`, `csrf_token`.
* Guard = `(client)/layout.tsx` (server). `/login?next=<path>` round-trip is validated by `returnPath()`.
* Sign-out: `WorkspaceShell` POSTs `/api/auth/logout`, calls `clearSession()` (drops CSRF + upload sessions), `router.push('/login')`.
* After login, the login page does not redirect already-signed-in users away from `/login` (**unverified**; no such check seen).
* SSO: `/v1/sso/discover` decides password vs SSO from the email domain; the browser then does a full navigation to `start_url`. `remember=false` appended for "keep me signed in" off.

---------------------------------------------------------------------------------------------------

## 4. Design system, styling, accessibility

### 4.1 Styling

| Aspect | Finding |
|---|---|
| Approach | Global CSS imported in `app/layout.tsx`: `tokens.css` (138 lines, palette, shape, depth, layout vars), `base.css` (64), `components.css` (850, buttons/cards/pills/tables/forms/dialogs), `shell.css` (140, sidebars/topbars), `auth.css` (115), `ask.css` (200). Class naming is flat and short (`.pg`, `.head`, `.card`, `.btn.primary`, `.trow`, `.pill`, `.kpi-tile`) with utility helpers (`.stack-14`, `.w760`, `.row-12`, `.mut`) |
| Tokens | Pastel palette ("owner decision 2026-10-23", Claude Design canvas): bg `#F7F8FD`, surface white, ink `#1B2140`, accent blue `#3F5AE0`, lavender `#F0EAFF`/`#5B3FC4`; status pills mint/butter/rose; admin console tokens `--admin-*`; radii `--r-*` (20px cards, 22px composer, 12px buttons); only 6 named shadows; layout vars `--sidebar 264px`, `--admin-sidebar 248px`, `--chat 780px`, `--page 1100px` |
| Theme | **Light only** (`color-scheme: light`; no dark mode, no `prefers-color-scheme`). The admin console uses a light periwinkle sidebar too (the old "navy" wording survives in comments and `AdminShell` docs) |
| Fonts | Self-hosted **Hanken Grotesk** (latin 400/500/600/700, woff2, `font-display: swap`) loaded via `@font-face` in `tokens.css`; no italics; fallback `system-ui, sans-serif`. Licence file included. `font-src 'self'` in CSP |
| Icons | Inline SVG paths in `ui/design.tsx` (`NavIcon`) + `AuthFrame` icons; `lucide-react` only once |
| Logo | `LogoMark` hard-codes `#101828`/`#2F4BE0`/`#7C93FF` (not tokens) |
| CSP-driven rules | No inline `style=` attributes anywhere (grep `style={{` = 0 hits); progress bars are SVG `rect` widths or `<progress>`; skeletons are classes. `react/no-danger` is an ESLint error and no `dangerouslySetInnerHTML` exists |
| Responsive | Breakpoints at 760 px (sidebar becomes overlay menu; page behind `inert`) and 1024 px (auth pitch column moves under the card). Replica Playwright config exercises 1360 and 375 widths |
| Motion | Short hover transitions, pulse/spin/shimmer keyframes; `prefers-reduced-motion` neutralises them all |
| Icon/favicons | `app/icon.svg` only; no `apple-touch-icon`, manifest, robots or sitemap |

### 4.2 Accessibility (what the code does; not audited with a screen reader)

* Skip link "Skip to content" in both shells; `<main id="main" tabIndex={-1}>`; landmark `aria-label`s on navs ("Main", "Recent chats", "Deep dive", "At a glance").
* Focus ring: global `:focus-visible` 2 px accent outline, 2 px offset.
* Mobile menu: `aria-expanded`/`aria-controls`, Escape closes, Tab trapped inside, focus returns to the Menu button, rest of page `inert`.
* `aria-current="page"` on nav; `aria-busy`/`aria-label` on loading skeleton; `role="alert"`/`status` on notices; tables use `scope="col"`; sr-only labels for icon/ambiguous links.
* Pills always carry the word, not colour alone (token comment: "always with the word written in the pill").
* `ui/contrast.ts` + `tests/unit/ui-tokens.test.ts` check WCAG contrast of token pairs.
* E2E: `@axe-core/playwright` (wcag2a/2aa/21a/21aa) in `e2e/helpers.ts` and `e2e/replica/screens.spec.ts` (fails on serious/critical, and on any console error including CSP reports, horizontal overflow, "not found").
* Gaps noticed: no dark mode; `themeColor` mismatch; no `global-error` boundary; chart-like components (`TokenMeter` bar, `ContextMeter` ring) rely on text alternatives that I did not individually verify (**unverified**).

---------------------------------------------------------------------------------------------------

## 5. How the browser reaches the API

### 5.1 Request path (BFF proxy, not a rewrite)

```
Browser  --(same origin, cookie __Host-ai_session)-->  Next.js (web)
   /v1/**            -> app/v1/[[...path]]/route.ts   --(server-side fetch to API_ORIGIN, cookie + request id)-->  FastAPI /v1/**
   /api/operator/v1/** -> app/api/operator/v1/[...path]/route.ts --(Bearer = Google ID token cookie)--> FastAPI /operator/v1/**
   Server Components  -> lib/server-api.ts            --(same, direct)-->  FastAPI
   Uploads: browser PUT directly to object storage (UPLOAD_ORIGIN) with resumable session URLs from the API
```

* `README.md`, `Dockerfile` comment and the header comment of `next.config.ts` and `lib/api.ts` say `/v1` is "rewritten"; **there is no `rewrites()` in `next.config.ts`**. It is a route handler (`app/v1/[[...path]]/route.ts`), which is why it can cap bodies, enforce timeouts and filter headers. Comment drift only.
* Tenant proxy rules (`app/v1/[[...path]]/route.ts`): unsafe methods require same-origin `Origin` (else 403); `API_ORIGIN` unset in production -> 503; forwards only these request headers: `accept, content-type, idempotency-key, if-none-match, if-range, last-event-id, origin, range, traceparent, x-csrf-token, x-forwarded-for` plus a fresh `x-request-id`; forwards only the 3 auth cookies; streams request bodies (cap 1 MiB, or 21 GiB for `PUT /v1/documents/{id}/content`) and counts bytes; DELETE bodies are buffered (a streamed DELETE body did not reach the API); `redirect:"manual"`; response headers copied except content-encoding/length/transfer-encoding/connection; `Set-Cookie` appended individually; timeouts 120 s for JSON, 60 min for downloads/exports/CSV/xlsx/uploads; SSE requests (`Accept: text/event-stream`) follow only the client's abort signal; errors become problem+json 413/504/503 and log only the path (never the query).
* `x-forwarded-for` from the browser request is forwarded as-is (relies on the load balancer overwriting/appending it; **unverified** in the infra).

### 5.2 Cookies and CSRF

| Cookie | Set by | Purpose |
|---|---|---|
| `__Host-ai_session` | API (via proxy) | Tenant session (httpOnly, Secure, path `/`) |
| `__Host-ai_preauth`, `__Host-ai_preauth_once` | API | SSO pre-auth marker and the "keep me signed in = off" marker |
| `__Host-6th_operator_session` (HTTPS) / `6th_operator_session` (http dev) | `/api/operator/callback` or `/api/operator/session` | Staff session = the Google ID token (SameSite=Strict, httpOnly) |
| `__Host-6th_operator_oauth_{state,nonce,verifier,return}` | `/api/operator/login` | 10-minute OIDC handshake state (SameSite=Lax, httpOnly) |

CSRF: double-protection of (a) same-origin `Origin` check in the web proxy for all non-GET calls, and (b) `X-CSRF-Token` header (value from `GET /v1/sso/session`, held in memory only, replayed once on `csrf_refused`). Login/forgot/reset/invite/verify use `apiPublic` (no token). Whether the API also checks Origin/SameSite is outside this audit.

### 5.3 Security headers/CSP
See 1.3. `unsafe-eval` only in dev. No `unsafe-inline` for scripts or styles. `connect-src` allows only self and `UPLOAD_ORIGIN` (a bare `http(s)://host[:port]`, anything else ignored). Test coverage: `tests/unit/security-headers.test.ts`.

### 5.4 Environment variables used by the web tier

| Variable | Read in | Meaning | In `.env.example`? | Set in infra |
|---|---|---|---|---|
| `API_ORIGIN` | `lib/api-origin.ts` | API base URL. Required in production (fail closed) | yes | `infra/stack/main.tf` |
| `UPLOAD_ORIGIN` | `proxy.ts` | Object-storage origin added to CSP `connect-src` | **no** | `https://storage.googleapis.com`; replica compose `http://localhost:8092` |
| `OPERATOR_GOOGLE_CLIENT_ID` (+`_FILE`) | `lib/operator-oidc.ts` | Google OAuth client id for staff sign-in | yes | tf |
| `OPERATOR_GOOGLE_CLIENT_SECRET` (+`_FILE`) | same | OAuth secret (Secret Manager in cloud) | yes | tf secret `operator-google-client-secret` |
| `OPERATOR_GOOGLE_REDIRECT_URI` | same | Must be `<origin>/api/operator/callback` | yes | tf |
| `STAFF_WORKSPACE_DOMAIN` | same | Workspace domain allowed (`hd` hint) | yes | tf |
| `STAFF_EXTERNAL_EMAILS` | same | If non-empty, drops the `hd` hint so personal Google accounts can be named staff | **no** | tf |
| `NEXT_DIST_DIR` | `next.config.ts` | Alternate build dir for a parallel dev server | no | n/a |
| `PORT`, `HOSTNAME` | Next standalone server | Listen port/host | n/a | Dockerfile sets `HOSTNAME=0.0.0.0` |
| `NODE_ENV` | many | Dev/prod switches (HSTS, CSP eval, API fallback, cookie names) | n/a | |
| Test only | `playwright*.config.ts`, `e2e/` | `E2E_API_ORIGIN`, `E2E_WEB_ORIGIN`, `REPLICA_WEB_ORIGIN`, `DEMO_PASSWORD` | | |

There are **no `NEXT_PUBLIC_*` variables**; nothing is baked into the client bundle (good, and the README claims the same).

---------------------------------------------------------------------------------------------------

## 6. Tests

| Suite | Location | Size | What it covers |
|---|---|---|---|
| Unit (Vitest) | `web/tests/unit/` | **50 files, 4,443 lines**, 30 use jsdom | Pure libs (`query`, `render`, `same-origin`, `tenant-cookies`, `console`, `lib-moves`, `parity-lib`, `phase4b-lines`, `ws-copy`); contract/proxy (`api-contract`, `v1-proxy`, `operator-proxy`, `operator-oidc`, `operator-oauth-routes`, `operator-api`, `server-api`, `security-headers`); components (Ask stream, answer views/markdown/structured, sheet/tab views, archive pages, parity workspace, mail-drive, drive chooser, documents/uploads, research screens/board hardening, token meter, usage proceed, resume, delete account, live sync, route states, sign-in/auth pages, what-was-read, uploaded files, ui tokens contrast) |
| E2E (Playwright) | `web/e2e/` | 4 specs + helper: `tenant.spec.ts` (53 lines), `run-download.spec.ts`, `parity.spec.ts`, `helpers.ts`; `e2e/replica/screens.spec.ts` | `playwright.config.ts` starts `uv run python -m tests.e2e.serve` (API on 8088 with fake IdP, `LLM_FAKE=hermetic`) and the **built standalone** server on 3008; runs workers=1. The replica config hits a running replica stack (3010) and checks every screen at 1360 and 375 px with axe, console errors, overflow |
| Screenshot scripts | `web/scripts/shots.mjs`, `phase4b-shots.mjs`, `preview-shot.mjs`, `preview-structured.tsx` | | manual helpers, not tests. `web/shots/` is an empty directory |

Staleness found (static reading; the suites were **not run**):
* `e2e/tenant.spec.ts` "account page shows its tabs" expects a navigation named "Account sections" with links Overview/People/Jobs/Technology/News/Sources/Files and a **button** "Refresh from web". The current page has a "Deep dive" nav with different labels and "Refresh from web" is a **link**. Likely failing.
* Same spec, "ask answers..." expects a button group "Search mode" with "Normal" pressed, a link "New chat", and `.q` class. Current composer has a "Search" toggle with `aria-pressed`; the sidebar "New chat" is a **button**; likely failing.
* Same spec, "removed screens redirect" includes `/audit`, which has no page and 404s.
* Unit tests for the removed settings screens appear to be gone (good), but `lib/query.ts` still has a test though nothing uses it.
* Whether `make check`/CI runs e2e: only the unit job is clearly in CI (`ci.yml` job `web`); e2e job exists later in the file (**unverified** if it is required).

---------------------------------------------------------------------------------------------------

## 7. Risks, dead code, unfinished or hard-coded things

| # | Severity | Finding | Where |
|---|---|---|---|
| 1 | Medium | **Strict response schemas = lockstep deploys.** Generated Zod objects are `.strict()` and both `api()` and `serverGet()` parse replies; an API that adds a field (or a stale generated client) turns a working screen into "could not be loaded"/502 `contract_mismatch`. `scripts/gen_web_client.py --check` in CI guards drift, but rollbacks of only one side are dangerous | `lib/api.ts`, `lib/server-api.ts`, `lib/gen/api.gen.ts` |
| 2 | Medium | **Auth is enforced only by data calls, not by routing.** `proxy.ts` has no auth. Tenant pages rely on `(client)/layout.tsx` server reads; operator pages ship their HTML to anyone and rely on a client-side gate + the API rejecting data calls. Not a leak (no data in the shell) but means "who can see page markup" is public | `proxy.ts`, `operator/(console)/OperatorSessionGate.tsx` |
| 3 | Medium | Operator session cookie **is the raw Google ID token** (about 1 h lifetime from `exp`); no server-side session or revocation in the web tier. `POST /api/operator/session` accepts any bearer token from the page body and sets it after API verification, an alternative sign-in path whose production use is unverified | `api/operator/callback`, `api/operator/session` |
| 4 | Low | `x-forwarded-for` is forwarded from the client request. Safe only if the edge overwrites it | `app/v1/[[...path]]/route.ts` |
| 5 | Low | Logout expires `__Host-ai_session` and `__Host-ai_preauth` but not `__Host-ai_preauth_once` | `api/auth/logout/route.ts` |
| 6 | Medium (maintainability) | **14 + 4 redirect stub pages and 2 passthrough layouts** for removed screens. README and `docs/frontend/*` still describe them as live | `app/(client)/{alerts,chat,downloads,runs,usage,settings/**}`, `app/operator/(console)/{cost,spend,support,tenants}` |
| 7 | Medium (docs) | **Docs drift**: `web/README.md` route table lists `/settings/**`, `/runs`, `/alerts`, `/chat`, `/downloads`, `/usage` as screens; `docs/frontend/account-intelligence-ui-capability-map.md` documents `/runs`, `/chat`, `/alerts`, `/settings/*`, `/audit` as connected; `docs/frontend/operator-route-map.md` lists `/operator/tenants`, `/configuration`, `/support`, `/spend`, `/cost` etc. with endpoints (`/operator/v1/tenants`, `flags`, `keys`, `pilot/aggregate`) that no current page calls; `docs/guide/03-code-map.md` is the one that is accurate ("Many old screens now just redirect to `/`") | docs |
| 8 | Low | Dead code: `lib/query.ts` (+ `GET /v1/accounts/{d}/tables/{t}` client op) used only by a test; `pageExtensions` `dev.tsx` + `/__design` comment with no such files; `@vitest/coverage-v8` unused; `lucide-react` used for a single icon; `ui/contrast.ts` test-only (fine) | listed files |
| 9 | Low | Two competing component/CSS vocabularies (`ui/design.tsx` vs `ui/primitives.tsx`, `.pg` vs `.page`); `/runs/[run_id]` is a fully client-rendered page unlike its siblings | `ui/`, `runs/[run_id]/page.tsx` |
| 10 | Low | `lib/messages.ts` claims to hold every user-facing string but 11 files use it; most copy is inline and scattered (34 "owner decision/request" comments embed product history in code) | `lib/messages.ts` |
| 11 | Low | Hard-coded values: logo colours in `ui/design.tsx`; `themeColor "#F5F6F8"`; `NO_LIMIT = 1_000_000` sentinel meaning "caps are off" (`lib/limits.ts`, tied to backend `dev_limits UNCAPPED`); product names "Account Intelligence by 6th" and "6th Admin" in many places; `accounts?limit=200` hard cap on the Ask account list and the accounts page (no pagination in UI); sheet paging constants (200 / 500); proxy timeouts 120 s / 60 min; `https://accounts.google.com` / `oauth2.googleapis.com` in operator routes; research-live poll/backoff constants | various |
| 12 | Low | `operationFor()` / `contractFor()` linearly scan all ~220 operations and rebuild a regex per candidate on every call (small CPU waste, no cache) | `lib/api.ts`, `lib/server-api.ts` |
| 13 | Low | Layout calls four API reads on every navigation (`force-dynamic` + no caching) and each page does more; the comment notes this was already reduced from 3 sequential rounds. Large accounts list (200) is fetched for Ask on every `/` visit | `(client)/layout.tsx`, `(client)/page.tsx` |
| 14 | Low | No `global-error.tsx`, no `robots`/manifest/apple icon, no `public/` | `app/`, `public/` |
| 15 | Info | `web/.env.local` and `.next/`, `tsconfig.tsbuildinfo` exist in the working copy (all gitignored). `shots/` is an empty folder | `web/` |
| 16 | Info | No TODO/FIXME/HACK markers in app, components, lib or ui (grep = 0). Unfinished work is instead visible as the "Removed from the product" stubs | |
| 17 | Info | Terraform runs the web service on port 3000 while the Dockerfile exposes 8080 and the standalone server uses `$PORT`: confirm the deployed port is consistent (**unverified**) | `infra/stack/main.tf`, `web/Dockerfile` |

Positive controls observed: raw HTML injection banned by lint and absent; all model/server text rendered as React text nodes via `cellText`/`safeLink`; Markdown goes through `rehype-sanitize`; strict nonce CSP with no inline styles/scripts; same-origin + CSRF on writes; body-size and time limits on the proxy; production fails closed without `API_ORIGIN`; no secrets or `NEXT_PUBLIC_` vars in the client bundle; source maps removed from the image; non-root container with digest-pinned base.

---------------------------------------------------------------------------------------------------

## 8. Summary (10 lines)

1. Next.js 16.4.1 App Router + React 19.3 + TypeScript 6, plain hand-written CSS with tokens (no Tailwind), zod 4, only 7 runtime dependencies (3 serve Markdown answers).
2. One site serves three audiences: public sign-in/legal, the tenant workspace (`(client)`), and the staff console (`/operator`), all with route handlers acting as a backend-for-frontend.
3. The browser only ever talks to its own origin; `/v1/**` is a streaming route-handler proxy (not a rewrite) that filters cookies/headers, checks Origin, caps bodies and times out; file uploads go straight to object storage.
4. All API traffic is validated by zod schemas generated from OpenAPI (`lib/gen/api.gen.ts`); schemas are strict, so API and web must be deployed together.
5. Server Components fetch via `serverGet`; the `(client)` layout is the only auth gate; operator pages use a client-side session gate; auth cookie never reaches JS.
6. Live data uses SSE (`EventSource` for research board and run progress, `fetch` stream for Ask answers) with polling fallbacks and `router.refresh()` loops.
7. Strong security posture (nonce CSP, no inline styles, sanitised Markdown, CSRF, same-origin checks, fail-closed config); main weak spots are lockstep schema coupling and the staff cookie being the raw Google ID token.
8. 34 real screens, 18 redirect-only stubs for removed features, 8 route handlers; docs (`web/README.md`, `docs/frontend/*`) are stale and e2e `tenant.spec.ts` appears out of date.
9. Tests: 50 Vitest files (4.4k lines) plus Playwright e2e + axe screen sweeps; CI clearly runs typecheck, lint, unit tests and build; e2e currency unverified.
10. Cleanup candidates: delete the stub pages/layouts, `lib/query.ts`, the `dev.tsx` page extension, unused coverage dep, single-use `lucide-react`; unify `ui/primitives` with `ui/design`; refresh the docs.

**Count of pages found: 52 `page.tsx` files (34 real screens + 18 redirect stubs), plus 8 route handlers.**
