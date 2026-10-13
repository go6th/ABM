# Screen to API map

Capabilities come from authenticated `GET /v1/me`; the browser keeps no role-to-screen policy. API authorization stays authoritative even when a control is hidden. Design rules: white background, one accent, system font, 6px corners, 1px borders, no shadows, 220px sidebar, chat column 720px, tables 1100px.

## Client workspace (`web/app/(client)`)

| Screen | Route | API surface | Notes |
|---|---|---|---|
| Ask (home) | `/`, `/?thread=`, `/?account=` | `POST /v1/chat/threads`, `POST /v1/chat/threads/{id}/turns/stream`, `GET /v1/chat/threads/{id}`, `GET /v1/accounts` | Normal mode streams from the workspace; `[F3]` citations become numbered links to the Sources tab. Deep mode starts a run (`POST /v1/runs {domain, depth: "deep"}`) for the chosen account, shows the steps and checkpoint card, then answers. |
| Accounts | `/accounts` | `GET /v1/accounts`, `GET /v1/signals` | Table: account, last researched, schedule, new signals (last seven days). |
| New research | `/research/new`, `?mode=file` | `POST /v1/runs` (company or file), `POST /v1/documents/uploads`, `POST /v1/documents/{id}/complete`, `GET /v1/documents/{id}/status`, `GET /v1/documents`, `POST /v1/documents/confirm-company`, `POST /v1/documents/estimate` | Two tabs. File tab has drop area, Choose from storage, company confirmation, "Also check public sources" off by default. No spend caps. |
| Run | `/runs/{id}` | `GET /v1/runs/{id}`, `/progress`, `/events`, `/spend/live`, `/cap`, `POST .../cap/approve`, `POST .../approve`, `POST .../cancel`, `GET /v1/exports` | Eight skill steps mapped from the 13 workflow stages; checkpoint card with Continue, Continue 5 times, Auto continue (kept by the open page), Stop and keep results. |
| Account | `/accounts/{domain}` and tabs `people`, `jobs`, `technology`, `news`, `sources`, `files` | `GET /v1/accounts/{domain}`, `/tables/{people,jobs,tech,facts}`, `/history`, `GET /v1/signals`, `GET/POST /v1/exports`, `GET /v1/runs/{id}/internal` | Overview: summary, Key findings, What changed (only after a second completed run), file analysis for file-mode accounts. Tables sort and filter server-side; Download as Excel builds a CSV package. Schedule line is kept in the browser until a schedule endpoint exists. |

Removed from the product: alerts, audit, usage, downloads, feedback, research files, every settings page (connectors, CRM, SSO, users, support, model key), the account cap meter and the 6th Library picker. Their routes redirect to `/` until the files are deleted (see `web/LEGACY_TO_DELETE.txt`).

## 6th admin console (`web/app/operator`)

| Screen | Route | API surface | Notes |
|---|---|---|---|
| Overview | `/operator` | `GET /operator/v1/tenants`, `/pilot/aggregate`, `/ops/stuck` | Counts only. Isolation check is marked "not reported by the API yet". |
| Clients | `/operator/clients` | `GET /operator/v1/tenants`, `POST .../suspend`, `POST .../reactivate` | Enable or disable a client. No caps. |
| Users | `/operator/users` | `GET /operator/v1/tenants`, `/pilot/aggregate` | Per-client counts; per-user rows need an operator users endpoint. |
| Runs | `/operator/runs` | `GET /operator/v1/ops/stuck`, `/ops/failures`, `/runs/{id}/spend/live`, `/runs/{id}/timeline` | Read only; a full run list needs an operator runs endpoint. |
| Settings | `/operator/settings` | `GET/POST /operator/v1/flags`, `GET /operator/v1/keys` | Checkpoint size and model routing stored as platform flags (`checkpoint_unit`, `model_phase1`, `model_phase2`). Rate table and skill registry not connected. |

## Current-user response

`GET /v1/me` returns the verified user and tenant identifiers, role, capability names and feature flags. The Next server layout validates this payload against the generated OpenAPI Zod schema before rendering the authenticated shell. A 401 redirects to sign-in; unavailable or malformed identity metadata fails closed. `plan_limits` is no longer read by any screen.
