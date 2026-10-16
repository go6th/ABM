# `web/`: the Next.js app

The screens of the product: the client workspace, sign-in, and the 6th operator console. Next.js
16 (App Router, React 19, TypeScript), built as a standalone server (`web/Dockerfile`). The
browser talks only to this origin; everything under `/v1/*` is proxied to the API server-side
with the session cookie (`app/v1/[[...path]]/route.ts`), so no token and no API address ever
reaches the client bundle. `proxy.ts` sets a per-request Content-Security-Policy nonce; the
ESLint config forbids raw HTML injection.

## Commands

```bash
npm ci                      # install (once)
npm run dev                 # http://localhost:3000 against API_ORIGIN (see .env.example)
npm run typecheck && npm run lint && npm test   # the gates CI runs
npm run build               # production build (+ postbuild copies the standalone assets)
npm run e2e                 # Playwright (needs the stack: make e2e)
```

## Routes

| Route group | Path | What it is | Server side |
| --- | --- | --- | --- |
| `(auth)` | `/login`, `/forgot`, `/reset`, `/invite`, `/verify-email` | client sign-in and account flows | `POST /v1/auth/*`, `/v1/sso/*` |
| `(client)` | `/` | Ask: the workspace answer box with numbered citations | `POST /v1/chat/ask`, `/v1/chat/threads/*` |
| | `/accounts`, `/accounts/add` | the accounts list; add an account three ways | `/v1/accounts`, `/v1/accounts/from-library`, `/v1/research` |
| | `/accounts/[domain]` | the Account workspace: summary, deliverables, uploaded files, what was read, resume | `/v1/accounts/{domain}/workspace`, `…/deliverables`, `…/download-everything`, `/v1/documents`, `/v1/research/resume` |
| | `/accounts/[domain]/[sheet]` | the deep-dive sheets (people, jobs, technology, news, sources) with CSV and Excel download | `/v1/accounts/{domain}/sheets/{sheet}`, `…/csv`, `…/xlsx` |
| | `/accounts/[domain]/refresh`, `/accounts/[domain]/files` | Refresh from web (scopes, estimate); the account's uploaded files | `/v1/accounts/{domain}/refresh`, `/v1/credits`, `/v1/documents/*` |
| | `/research/new` | New research: company name or upload a file | `/v1/research`, `/v1/research/estimate`, `/v1/documents/uploads`, `…/complete`, `…/confirm-company`, `…/estimate` |
| | `/runs`, `/runs/[run_id]` | research runs and one run's steps | `/v1/runs`, `/v1/runs/{run_id}`, `…/progress`, `…/events`, `…/collected` |
| | `/schedules`, `/alerts`, `/chat`, `/downloads`, `/usage`, `/mail-and-drive`, `/model-preferences`, `/support-access` | the remaining client screens | the route of the same name |
| | `/settings/**` | tenant settings: users, security, SSO, connectors, CRM, alerts, model key, support | `/v1/settings`, `/v1/users`, `/v1/sso`, `/v1/connectors`, `/v1/crm` |
| `operator` | `/operator/login`, `/operator/**` | the 6th console: clients, users, runs, spend, cost, library, defaults, support, view-as | `/operator/v1/*` through the BFF routes under `app/api/operator/*` (Google OIDC) |
| probes | `/healthz`, `/readyz` | liveness; readiness that also asks the API | |

## Folders

| Folder | What it holds |
| --- | --- |
| `app/` | the routes above (one `page.tsx` per screen, layouts, route handlers) |
| `components/accounts/` | the Account workspace pieces: AddAccount, NewResearch, ResearchCompany, RefreshForm, ResumeResearch, SheetView, FilesCard, UploadedFilesCard, WhatWasRead, DownloadEverything, SchedulesView, MailDrive, SupportAccess, AccountAsk |
| `components/ask/` | Ask, AskComposer, AnswerView |
| `components/ws/` | the workspace shell, run steps, document drop, drive chooser, file analysis |
| `components/*.tsx` | Limits, ModelMenu, ModelPreferences, ProviderKey |
| `lib/` | `api.ts` (the browser's one door to the API), `server-api.ts` (Server Components' door), `api-origin.ts`, `upload.ts` and `upload-store.ts` (resumable uploads straight to object storage), `sse.ts` (the Ask stream), `query.ts`, `render.ts` (safe rendering), `messages.ts` (every user-facing string), pure formatters, `gen/` (the generated API types and labels; never edit by hand) |
| `ui/` | design tokens and base CSS, primitives, the auth frame, route error |
| `tests/unit/` | Vitest tests of the pure modules and components |
| `e2e/` | Playwright: tenant flows, run download, and `e2e/replica/` (every screen at two widths with axe, CSP and overflow checks; `playwright.replica.config.ts` runs it against `make replica-up`) |
| `scripts/` | `copy-standalone-assets.mjs` (postbuild), `shots.mjs` and `phase4b-shots.mjs` (screenshot click-throughs) |
| `public/` | static assets (empty today) |

`AGENTS.md` is written by `next dev` (Next.js guidance for agents); leave it in place.
