# Replica plan: the Claude Design prototype, front end and back end

Spec: `docs/build/REPLICA_PROMPT.md` (owner decision, 2026-10-14). It wins over `BUILD_PROMPT.md`,
`DESIGN_COPY_PROMPT.md`, `VISUAL_PROMPT.md` and `CHANGE_CLAUDE_ONLY_SEARCH.md` where they conflict.
The engineering rules of `BUILD_PROMPT.md` still apply (forced RLS, paid-call ledger, idempotent tool
calls, grading in code, the pre-delivery gate, the skill as research engine, the two-output rule,
prompt-injection protection, security, tests).

Branch: `feat/replica`, created from `feat/final-product` at `9547f9a`. The folder is shared with
another session that has uncommitted edits in `src/agents/`, `config/tracker_v1.yaml` and `tests/`.
I do not touch those files. Nothing is committed until the owner says so.

## 1. Sources and how conflicts are resolved

| Source | Role |
| --- | --- |
| `docs/design/claude-design/*.html` | Markup, CSS, copy and click behaviour (wins over the PDF) |
| `Account Intelligence by 6th (2).pdf` | The picture; the only source for the "What this answer used" panel (page 4) |
| `DESIGN_COPY_PROMPT.md` 1.1 to 1.6 | Tokens, type scale, radii, shadows, components |

Where the three disagree, I chose as follows. Each is reversible.

| Place | HTML | PDF | Prompt | Built as |
| --- | --- | --- | --- | --- |
| People filter chips | All priorities, Priority A, B, C | All 41, Priority A · 8, Technology · 17, Commercial · 11, Changed this month · 2 | The PDF list | The prompt's list with counts (it names them explicitly) |
| People table | Spreadsheet `table.xl` with row numbers | Card rows with initials | Spreadsheet | Spreadsheet |
| Account rail files | FILES with checkboxes | Download Summary (Word), Download tables (Excel) | FILES list | FILES list |
| Offer card copy | "Want anything newer? Your workspace was refreshed N days ago. I can check the web, only if you confirm." | Two-line version | HTML copy | HTML copy |
| Answer header | none | "Answered from your workspace" with a dark mark | Panel from PDF | Keep the PDF line above the answer (it states the source in words) |
| Add account | Three numbered sections | Library grid plus "Your selection" rail | HTML | HTML |
| Probable pill | Blue `#E6F0FF` / `#1D5FC4` | Green on one page | Blue | Blue |
| `RECENT` label colour | `#98A2B3` (fails contrast) | same | `--muted` | `--muted` `#667085` |
| Admin "Access ended" pill | `#FDE7E7` | | `#FEE4E2` (tokens) | `#FEE4E2` |
| Sign out | absent | absent | Under the email, secondary text link | As the prompt says |

## 2. Screens, controls and what is behind them

"Exists" is what the repo has today (verified on 2026-10-14). "Build" is the work.

### Shared user shell

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Logo, Workspace card | `GET /v1/me` (`tenant_name`) | Yes | Restyle to design |
| New chat | client state, `/` | Yes | Restyle |
| Nav: Ask, Accounts, New research, Schedules, Mail and drive | routes | Ask, Accounts, New research | Add Schedules, Mail and drive; hide Mail and drive when the client feature is off |
| RECENT | `GET /v1/chat/threads` | Yes | Restyle |
| Accounts used N of M | `GET /v1/me` gains `accounts_used`, `account_cap` | No | `accounts.added_by`, client settings |
| Avatar, name, email, Sign out | `/v1/me`, `/api/auth/logout` | Yes | Restyle |
| Notifications (not in design) | `GET /v1/notifications`, `POST /v1/notifications/{id}/read` | No | New table `notifications` |

### 1 Ask (`/`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Heading and sub-line with N accounts | `/v1/accounts` count | No (removed earlier) | Restore |
| Composer, Enter sends | `POST /v1/chat/threads`, `.../turns/stream` | Yes (raw text) | Structured stream: `text`, `citations`, `used`, `offer`, `followups`, `done` |
| + Upload a file | link `/research/new?tab=file` | Partly | Link |
| Scope chip (All N accounts / one account) | client state, `account_id` on the turn | Partly | Menu button with the account list |
| Web: off chip | opens Refresh (`/accounts/{d}/refresh`) for the scoped account, or asks to pick one | No | Toggle with `aria-pressed`; never searches silently |
| Suggestion chips | `GET /v1/chat/suggestions` (from recent signals, findings and accounts, at most five) | No | New route, computed from stored rows |
| Thread: user bubble, searching state, paragraphs, citations, item card, source card | stream events | Partly | Rebuild to design |
| Offer card, Refresh from web, Stay local | `offer` event: newest cited source older than the account cadence, or no answer | No | Server decides; client renders |
| Follow-up chips (two) | `followups` event | No | Server proposes from the scoped account's stored rows |
| What this answer used panel | `used` event: workspace, web (always Not used in chat), mail and drive (Used / Not used / Not connected), SOURCES, Open account | No | New |
| Answers workspace-only | `chat/grounding.py` + citations; Claude when `ANTHROPIC_API_KEY` is set, an extractive local answerer otherwise | Grounding exists; no citations | Citation mapping in code; zero web calls asserted in a test |

### 2 Accounts (`/accounts`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Title, count, Add account | `GET /v1/accounts` | Yes | Restyle; button opens Add account |
| Rows: name and sub-line | new fields `new_signals`, `state` (`first_scan`, `no_change`, `researching`, `waiting_credit_limit`) | Partly | Derive from `account_runs`, `signal_events`, deltas |
| Lanes | `accounts.primary_lane` + fact lanes | Partly | `lanes` list on the account row |
| Last researched, Refresh cadence | `account_runs`, `accounts.refresh_cadence` | Partly | Relative date; cadence label |

### 3 Account workspace (`/accounts/[domain]`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Breadcrumb, title, sub-line | `GET /v1/accounts/{d}` | Partly | "Refreshed N days ago · <cadence> schedule · Web: not used for answers" |
| Schedule, Refresh from web | links to `/schedules#d`, `/accounts/{d}/refresh` | No | Links |
| THE ONE MINUTE VERSION | `narrative` with per-sentence source links | `report_sections` | Narrative endpoint field with `[n]` source refs |
| Key findings / What changed | `findings`, `changes`, `first_scan` | Partly | Grade pills, dates, No change row |
| Account composer | Ask with `account_id` | Partly | Navigates to Ask scoped to the account |
| DEEP DIVE counts | `rail` counts: signals, people, jobs, tech, news, mail and drive, sources | Partly | All seven counts |
| FILES, Download selected (N), Download all | `GET /v1/accounts/{d}/deliverables`, `.../deliverables/download?ids=` (zip for several) | Exports exist per package | New list and zip routes |

### 4 Refresh from web (`/accounts/[domain]/refresh`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Three scope cards with time and credit estimates | `GET /v1/accounts/{d}/refresh/estimate` | No | Rate table (`config/credits.yaml`) times the account's history |
| Credits left this month X of Y | `GET /v1/credits` | No | Monthly pool ledger |
| Confirm refresh | `POST /v1/accounts/{d}/refresh {scope, estimate_credits}`: reserves, creates the run | No | Reserve then enqueue; refuse with 409 `credits_short` |
| Stay local | back link | | |
| Confirm disabled when short | estimate vs left | No | Client and server check |
| Feature off | client setting `web_refresh_allowed` | No | Explains, nothing to confirm |

### 5 Schedules (`/schedules`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Row per account, next refresh, segmented control | `GET /v1/schedules`, `PUT /v1/schedules/{account_id}` | `accounts.refresh_cadence`; `src/refresh/schedule.py` pure | `accounts.next_refresh_at`; disable options faster than `fastest_schedule` |
| Estimated use per month, Pool | computed server-side from cadences and the rate table | No | Returned with the list and after each change |
| Worker runs due refreshes | worker tick `POST /tasks/schedules-tick` | `src/refresh/` pure, not wired | Draws from the pool; `waiting_credit_limit` when short |

### 6 Add an account (`/accounts/add`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| 1 Research a new company | `POST /v1/accounts/research {name, website, confirm_credits}` | `POST /v1/runs` | Estimate, confirm, reserve, start |
| 2 Ready-made research | `GET /v1/library/available`, `POST /v1/accounts/from-library {ids}` | No | Copy the published bundle into the tenant |
| Uses N of your M remaining | `/v1/me` cap | No | |
| 3 Analyze your own file | link `/research/new?tab=file` | | |
| Account cap enforced | server check in both add routes | No | 409 `account_cap_reached` |

### 7 People and the other deep-dive tables (`/accounts/[domain]/[table]`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Filter chips with counts | `GET /v1/accounts/{d}/tables/{table}?filter=&sort=&dir=&offset=&limit=` returns `facets` | `tables/{table}` exists | Facet counts, sort keys per table |
| Expand / Shrink | client state | | |
| Open in Excel | `GET .../tables/{table}/xlsx?filter=&sort=` | CSV only | Real `.xlsx` (openpyxl) |
| Spreadsheet table | server-side sort, filter, paging with absolute row numbers | Partly | Rebuild component |
| Tables: people, signals, jobs, tech, news, mail and drive, sources | same route | Partly | Mail and drive table shows only the caller's own items |

### 8 Mail and drive (`/mail-and-drive`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Mail and Drive cards, status, Connect / Disconnect | `GET /v1/connectors`, `POST /v1/connectors {kind}`, `GET /v1/connectors/oauth/callback`, `POST /v1/connectors/{id}/disconnect` | Routes and 0024 tables; providers NotConfigured | Per-user connections; "Not available yet" without OAuth app ids |
| Matched, last synced, shown only to you | sync job and match records | Partly (`framework.py`) | Match by account domain and names; private per-user facts |
| Feature off | client setting | No | Hidden from nav |

### 9 New research (`/research/new`) and 10 Analysis of your file (`/accounts/[domain]/files`)

| Control | API / data | Exists | Build |
| --- | --- | --- | --- |
| Tabs, company form, Start research | as Add account 1 | Yes (no credits) | Credit estimate and confirm |
| Drop area, Choose files, status pills | `/v1/documents/*` | Yes | Restyle; poll status |
| Which company | `POST /v1/documents/confirm-company` | Yes | Radio cards with mention counts |
| Also check public sources, Estimated cost | `POST /v1/documents/estimate` | Yes (units) | Credits: 0 when off |
| Analyze file | `POST /v1/runs {input_mode: file}` | Yes | |
| Analysis page sections, Not in your files, Files used, Delete these files | `GET /v1/runs/{id}/internal`, `DELETE /v1/documents/{id}` | Yes | Restyle; confirm dialog |
| Download internal summary | `GET /v1/runs/{id}/internal/file` | Yes | |

### Admin console (`/operator/*`)

| Screen | API / data | Exists | Build |
| --- | --- | --- | --- |
| A1 Overview | `GET /operator/v1/overview` (definer counts only) | Tenants overview only | KPIs, questions per week, categories, not using it, isolation pill |
| A2 Clients, Add client | `GET/POST /operator/v1/clients` | `/tenants` | Credits bar and status; invite link |
| A3 Client detail | `GET/PUT /operator/v1/clients/{id}`, publications | No | Settings, library publish, features |
| A4 Users | `GET /operator/v1/users`, `POST .../terminate`, `.../reenable` | No | Access state on memberships |
| A5 6th Library | `POST /operator/v1/library/uploads`, `GET /operator/v1/library`, `POST /operator/v1/library/publish` | No | Platform tables; client-ready check from the gate |
| A6 Runs and spend | `GET /operator/v1/runs`, credits by purpose | No | Read-only |
| A7 Platform defaults | `GET/PUT /operator/v1/defaults`, skill versions | Flags only | New client defaults, engine, safeguards, admins |
| View as a client | support grant (`admin/support.py`) | Yes | Read-only view, banner, audit; no question text or file contents |

## 3. Data model (new migration `0053_replica`)

Every tenant table: `tenant_id DEFAULT app_tenant()`, ENABLE and FORCE RLS, policy `tenant_isolation`,
an entry in `tests/isolation/matrix/matrix.yaml`.

| Table / change | Scope | Purpose |
| --- | --- | --- |
| `client_settings` | tenant | account cap per user, idle expiry days, monthly credit pool, fastest schedule, mail and drive allowed, web refresh allowed |
| `platform_defaults` | platform (operator writes only) | new-client defaults, credit cap per pull, model split, active skill version |
| `credit_entries` | tenant | append-only: `reserve`, `settle`, `release`; period `YYYY-MM`; purpose; run id; user. Left = pool − reserved − settled + released, per period |
| `accounts.added_by`, `accounts.next_refresh_at`, `accounts.refresh_scope` | tenant | cap count, scheduler |
| `account_runs.scope`, `.purpose`, `.credits_estimated`, `.credits_used`, status `waiting_credit_limit` | tenant | refresh scope and credits |
| `notifications` | tenant, per user | refresh finished, waiting: credit limit |
| `library_accounts`, `library_uploads`, `library_checks` | platform | admin uploads and client-ready checks |
| `library_publications` | tenant (RLS; written by an operator definer) | which library accounts a client may add |
| `question_categories` | tenant | counts only, per week |
| `isolation_checks` | platform | nightly result |
| `memberships.access_state`, `.last_active_at`, `.ended_at` | tenant | active, expired, terminated |
| connectors | reuse 0024 tables, add `owner_user_id` scoping where missing | private per-user items |

I keep `wallets` and `wallet_ledger` and their code as they are and do not use them for the pool: the pool resets
monthly and the wallet is a running balance. One ledger per purpose is clearer than overloading
the wallet. (Reversible: the pool can be moved onto the wallet later.)

## 4. Order of work (each step ends with a run of the stack and a screenshot comparison)

1. Design system (tokens, Hanken Grotesk self-hosted, components), user shell, demo seed, Ask with
   citations and the used panel. **Stop and show the owner the Ask screen next to PDF pages 1 and 4**
   (standing rule after the rejected restyle on 2026-10-13).
2. Accounts and Account workspace.
3. Deep-dive tables and xlsx export.
4. Credits, pool and Refresh from web.
5. Schedules and the scheduler tick.
6. New research and file analysis.
7. Add account with the library.
8. Mail and drive.
9. Admin console A1 to A7.
10. View as a client.
11. Tests (Playwright behaviour list in prompt section 5), contrast script, isolation entries, docs.

## 5. Blocked until the owner supplies something

- **Research runs that finish:** the ABM skill package (`skills/abm-account-research/`) and the
  model and data-tool keys. Until then refresh and new research run against the hermetic fakes;
  credits are reserved and settled from the fake ledger costs.
- **Mail and drive OAuth:** Google and Microsoft OAuth client ids and secrets (owner creates the apps).
  Until then the cards show "Not available yet".
- **Chat with Claude:** `ANTHROPIC_API_KEY`. Without it the local extractive answerer answers from
  stored rows with the same citations.

## 6. Decisions for the owner

- Credit rate table: credits per scope are placeholders (jobs 20, people 60, full 220, new company 220)
  until real ledger costs exist. One credit = ? dollars of provider and model spend.
- Whether "Questions answered locally" counts every answer that did not lead to a web refresh (my reading).
- Library bundle format: a zip per account with the five deliverables; which file names identify each one.

## 7. Progress

| Step | State (2026-10-14) |
| --- | --- |
| 1. Design system, shell, demo seed, Ask | Done; approved by the owner on 2026-10-14. |
| 2. Accounts and Account workspace | Done; checked against PDF pages 2 and 3 and the HTML at 1360px and 375px. |
| 3. Deep-dive tables | Done; People checked against the HTML (07) and PDF page 8 chip counts; the six other tables use the same view. |

Step 1 in detail:
- Tokens, Hanken Grotesk (self-hosted latin woff2 with its OFL licence), components, user and admin shells, sign-in pages restyled.
- Migration 0053 (`client_settings`, `platform_defaults`, `research_jobs`, `credit_entries`, `notifications`, `question_categories`, library tables, `isolation_checks`, access state on memberships, schedule and origin columns on accounts, per-user mail and drive columns). Applied and round-tripped on a separate local database, `ai_replica`, so the shared `ai` database is untouched.
- `scripts/seed_replica_demo.py`: the design's demo workspace (dev only, `.example` domains, `[Name]` placeholders, `DEMO_PASSWORD` from the environment).
- `POST /v1/chat/ask` (streamed structured answer), `GET /v1/chat/suggestions`, `/v1/me` with accounts used and the cap. Without a model key the answer is composed in code from stored rows; with `ANTHROPIC_API_KEY` Claude writes it from the same rows and every citation is checked.
- Tests: `tests/replica/test_answers.py` (12). Production build: zero CSP errors on Ask and sign-in.
- Not yet: the account pages the offer and the Web chip link to (`/accounts/{d}/refresh`) arrive in step 4, so those links 404 until then.

Owner decisions on 2026-10-14: demo users stay `analyst` (add a demo admin when local two-factor
works); People filter chips as the prompt lists them; credit rate placeholders stay until real runs;
the follow-up composer stays pinned at the bottom (as in the HTML design). The work moved to its own
worktree, `/home/sakib/abm-replica` on `feat/replica`; the main folder is the other session's.

Step 2 in detail:
- `GET /v1/accounts` rows carry the design's sub-line state (new signals since the previous
  refresh, first scan complete, no change, researching, waiting for credits), lanes, last
  researched and cadence. `GET /v1/accounts/{domain}/workspace` (narrative sentences with their
  sources, key findings, what changed only after a refresh, deep-dive counts with mail and drive
  limited to the caller, files), `GET .../deliverables` and `GET .../deliverables/download?ids=`
  (one file as itself, several or `all` as a zip; audited export).
- Seed: the design's three Recent chats with stored answers (one with first call questions, one with
  the offer card), 41 Highmark people, Priya's connected mail with 18 matched threads, and real
  Word and Excel deliverables for every account.
- Tests: `tests/replica/test_workspace_api.py` (10, real app on PostgreSQL), isolation matrix entries
  for the six new tenant tables and five new routes (isolation suite 478 passed).
- Production build: zero console and CSP errors on every built screen. Links to screens not built
  yet (Schedules, Refresh, Add account, the deep-dive tables) do not prefetch; until their steps,
  Schedules, Refresh and Add account show the not-found page, and the deep-dive links open the
  pre-replica table pages.

Step 3 in detail:
- `GET /v1/accounts/{domain}/sheets/{sheet}` for people, signals, jobs, technology, news,
  mail-and-drive and sources: fixed column and filter allow-lists, chip counts, sort and paging in
  SQL, absolute row numbers. Anything else is a 422; an unknown table a 404. The mail-and-drive
  sheet holds only the caller's own items and is labelled private.
- `GET .../sheets/{sheet}/xlsx`: the filtered, sorted table as a real workbook (a "Sheet 1" with a
  frozen header and an "About" sheet), audited as an export.
- One screen for all seven tables (`/accounts/{domain}/{sheet}`): chips with counts, Expand or
  Shrink, Open in Excel, the spreadsheet with sticky header, row numbers, sort arrows and
  `aria-sort`, and "Show more rows" past 200. The old per-table pages were removed.
