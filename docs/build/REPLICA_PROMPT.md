# Build prompt: an exact replica of the Claude Design prototype, front end and back end

You are working in the Account Intelligence repository at `/home/sakib/ABM Generator` (Next.js 16 web app in `web/`, FastAPI API and worker in `src/`, PostgreSQL with forced row level security, Redis, object storage). The owner designed the product in Claude Design. Build a **working replica** of that design: every screen must look the same, and every button, toggle, number and flow on it must work for real, backed by the API and database. Nothing on screen may be decorative or fake.

## 0. Read this first: this prompt changes earlier decisions

The owner has decided the product is the design, as designed. Where this prompt disagrees with `docs/build/BUILD_PROMPT.md`, `docs/build/DESIGN_COPY_PROMPT.md`, `docs/build/VISUAL_PROMPT.md` or `docs/build/CHANGE_CLAUDE_ONLY_SEARCH.md`, **this prompt wins**. Add a note at the top of each of those files: "Superseded where it conflicts by REPLICA_PROMPT.md (owner decision, 2026-10-14)". Record every reversal below in `docs/build/PLAN.md`.

Reversals (these features come **back** because the design has them):

| Earlier decision | Now |
| --- | --- |
| No credits, caps or pools (D2) | Build them as designed: monthly credit pool per client, account cap per user, credit cap per pull, credit costs shown before research, "Near credit limit" and "Waiting: credit limit" |
| No 6th Library (D1) | Build it: admin uploads finished account workspaces, checks them, publishes them to chosen clients; clients add them as ready-made research |
| No Mail and drive | Build it: read-only Gmail/Outlook and Google Drive/OneDrive/SharePoint connections per user, as designed |
| Normal / Deep, then "Research: off/on" chip | The design's **`Web: off`** chip. Answers come from the workspace; the web is used only through "Refresh from web", which the user confirms |
| Checkpoint "Continue?" approvals | Replaced by the design's model: credits are shown and confirmed **before** a refresh starts, and a run stops at the credit cap per pull |
| No Schedules page | Build the Schedules page as designed |
| Ask heading and suggestions removed | Keep them as designed ("What do you want to know?" and the suggestion chips) |
| Model picker (Opus 5.5, Fable 5.1, Kimi K3) | Not in the design: do **not** build it. The model split is the admin "Model split" setting |
| "What this answer used" panel removed | Build it as on PDF page 4 |

Everything in `BUILD_PROMPT.md` that is engineering rather than product still applies: tenant isolation with forced RLS, the paid-call ledger, idempotent tool calls, grading computed in code, the pre-delivery gate, running the ABM skill as the research engine, the two-output rule for file-derived facts, prompt-injection protection, the security requirements, and the test discipline.

## 1. Sources of truth

1. **The design as code:** `docs/design/claude-design/` (17 screens unpacked from `/home/sakib/Downloads/Account Intelligence by 6th.html`). Each file has the screen's markup, CSS and its interaction logic (`class Component extends DCLogic`, with `this.state`, handlers, `sc-if` / `sc-for` and `{{bound}}` values). The sample data and click behaviour in those scripts define how each control must behave.
2. **The picture:** `/home/sakib/Downloads/Account Intelligence by 6th (2).pdf` (18 pages). Where the HTML and the PDF differ, the HTML is newer and wins (for example, Add account in the HTML has three numbered ways; Probable is blue).
3. **The design system:** sections 1.1 to 1.6 of `docs/build/DESIGN_COPY_PROMPT.md` (font Hanken Grotesk self-hosted, every colour token, type scale, radii, shadows, components). Use them exactly. Ignore its section 2 (product changes); this prompt replaces it.

Another session may be working in this repo. Check `git status` and `git log -3`, work on a branch `feat/replica` from the current build branch, and commit only if the owner has said you may.

## 2. Screens and the features behind them

For each screen: build the UI pixel-close to the design, then wire every element to the API. Section 3 lists the API and data. Where the design shows sample numbers, show real numbers from the database.

### Shared user shell (every user screen)

- Logo, "Account Intelligence / by 6th".
- Workspace card: the client's name (from the tenant).
- **New chat** button: starts an empty Ask thread.
- Nav: Ask, Accounts, New research (shown as in the HTML for screens 9 and 10; include it in the nav on all screens), Schedules, Mail and drive. Active item styled as designed.
- **RECENT**: the user's latest chat threads by title; click opens the thread.
- Footer: **Accounts used N of M** with progress bar (N = accounts this user has added, M = the client's account cap per user), then avatar initial, name, email, and a Sign out control (add it under the email, same style as a secondary text link).

### 1. Ask (`01-home-chat`)

- Empty state: "What do you want to know?" and "Ask across your N accounts. Answers come from your workspace unless you choose the web."
- Composer: text field; chips **+ Upload a file** (opens New research, file tab), **All N accounts** (scope picker: all accounts or one account), **Web: off** (toggle; turning it on does not search silently: it opens the Refresh confirmation for the scoped account, or asks the user to pick an account if scope is all); **Ask** button; Enter sends.
- Suggestion chips under the composer: generated from the workspace (recent signals and accounts), five at most, clicking one asks it.
- Thread: navy user bubble; "Searching your workspace" state with a pulsing dot while the answer streams; answer paragraphs with **numbered citation chips**; an optional item card (for example `FIRST CALL QUESTIONS`) when the answer has a list; clicking a citation opens the **source card** under the answer (number, title, meta, Close; plus a link to open the source URL or file location).
- **Offer card** "Want anything newer? Your workspace was refreshed N days ago. I can check the web, only if you confirm." with **Refresh from web** (opens Refresh for the relevant account) and **Stay local** (dismisses for this answer). Show it when the answer's newest source is older than the account's refresh cadence, or the workspace could not answer.
- Follow-up chips after each answer (two related questions) and the follow-up composer at the bottom.
- **What this answer used** panel (PDF page 4): Your workspace (Used / Not used), Web (Used / Not used), Mail and drive (Used / Not used / Not connected), then **SOURCES** listing the cited items with dates, and **Open <account> workspace**.
- Answering is workspace-only (stored facts, tracker, files, connected mail and drive items, previous deliverables), with Claude as the model and citations to stored rows. It never goes to the web from chat.

### 2. Accounts (`02-accounts`)

- Title, "N accounts in your workspace", **Add account** button (opens screen 6).
- Table: Account (name plus sub-line: "2 new signals" in green, "First scan complete", "No change", or "Researching" / "Waiting: credit limit" while a run is open), Lanes, Last researched (relative), Refresh (cadence), `›`. Whole row opens the account.

### 3. Account workspace (`03-account-workspace`)

- Breadcrumb, account name, sub-line "Refreshed N days ago · <cadence> schedule · Web: not used for answers".
- **Schedule** button (opens Schedules focused on this account) and **Refresh from web** (opens screen 4).
- `THE ONE MINUTE VERSION`: the generated narrative; every statement links to its source.
- **Key findings** with grade pills and dates.
- On the first scan: "This is the first scan, so there is nothing to compare yet. After the next refresh, a What changed section appears here." After a later refresh: **What changed since <date>** with grade pills, and a "No change" row for quiet lanes.
- Account composer: "Ask about <account>", chips "<account> only" and "Web: off", Ask.
- Right rail **DEEP DIVE** with live counts: Signals, People, Job postings, Tech stack and teams, News, From your mail and drive, Sources. Each opens its table (People uses screen 7; the others reuse the same spreadsheet table).
- **FILES**: the account's deliverables (Account summary, People list, Jobs, Tech, Evidence) with checkboxes, **Download selected (N)** (one file downloads as itself, several as a zip) and **Download all**. The PDF version also shows "Download Summary (Word)" and "Download tables (Excel)"; the HTML file list replaces them.

### 4. Refresh from web (`04-refresh-from-web`)

- "Refresh <account> from the web?", last refresh age, "Nothing leaves your workspace until you confirm."
- Three option cards (radio): **Job postings only** (new and closed roles in your lanes), **People and teams** (senior people changes and team signals), **Full refresh** (news, jobs, technology, people and speaking). Each shows a time estimate and a credit estimate, computed from the rate table and the account's history.
- **Research credits left this month: X of Y** (the client's pool).
- **Confirm refresh** reserves the estimated credits and starts a research run with that scope; **Stay local** goes back. If the estimate exceeds the credits left, disable Confirm and say so. "You will be notified when it finishes" (in-app notification on the account row and in the sidebar's Recent area).
- If the client's "Web refresh by users" feature is off, this screen explains that and offers nothing to confirm.

### 5. Schedules (`05-schedules`)

- Intro: "Scheduled refreshes go to the web on your behalf, within your credit pool. Between refreshes, everything stays local."
- One row per account: name, "Next refresh in N days" or "No scheduled refresh", segmented control **3 days | Weekly | 2 weeks | Monthly | Off**. Options faster than the client's "Fastest allowed schedule" are disabled with a reason. Changes save immediately.
- Footer: **Estimated use: about N credits a month at these settings** (recomputed live) and **Pool: X credits a month · resets on the 1st**.
- The worker runs due refreshes (Full refresh scope unless the account sets otherwise), draws from the pool, and stops with "Waiting: credit limit" when the pool cannot cover it.

### 6. Add an account (`06-add-account`, HTML version)

- "Three ways to bring an account into your workspace."
- **1 · Research a new company**: Company name, Website, note on credits and timing, **Start research** (shows the credit estimate and asks to confirm, then starts a full research run).
- **2 · Ready-made research shared with your team**: the library accounts published to this client, as checkbox cards; accounts already in the workspace are listed as such; **Add N to workspace** with "Uses N of your M remaining accounts". Adding copies the published workspace into the client's tenant (no credits, available immediately).
- **3 · Analyze your own file**: **Upload a file** (screen 9, file tab).
- The **account cap** is enforced here: if the user is at the cap, the add buttons are disabled with "You have used all N accounts. Ask your administrator to raise the cap."

### 7. People (`07-people`) and the other deep-dive tables

- Breadcrumb, title, scope line ("Director and above, global, security excluded. Click a column heading to sort.").
- Filter chips with counts: All, Priority A, Technology, Commercial, Changed this month (active chip in navy).
- The expand toggle from the design (`expandLabel`: show more rows / fewer rows) and **Open in Excel** (downloads a real `.xlsx` of the current filter).
- Spreadsheet table: caption "People list.xlsx · Sheet 1 · N rows", sortable columns (row number, name, title, area, priority, grade, last seen) with arrows, sticky header, row numbers, copy-friendly cells. Sorting and filtering run on the server.
- Footnote "Only Confirmed facts should be said to a prospect."
- Reuse the same component for Signals, Job postings, Tech stack and teams, News, From your mail and drive, and Sources.

### 8. Mail and drive (`08-mail-and-drive`)

- Intro: read-only access; only items that match the user's accounts are used.
- **Mail** card (Gmail or Outlook): status pill Connected / Not connected, **Connect** or **Disconnect**; when connected: Matched to your accounts (N accounts · N threads), Last synced, Shown only to: You.
- **Drive** card (Google Drive, OneDrive or SharePoint): the same.
- **HOW IT IS USED** text as designed.
- Behaviour: OAuth with read-only scopes; a sync job matches items to the user's accounts by domain and names; matched items are stored as private, per-user facts labelled "From your mail and drive", never included in shareable deliverables; Disconnect revokes the token and deletes synced items. Hidden if the client's "Mail and drive connections" feature is off. Reuse `src/connectors_client/` (gmail, drive, oauth, quarantine, access_log, scopes) and migration 0024; finish what is NotConfigured. Real OAuth needs client ids and secrets the owner must create; until then show "Not available yet" instead of a broken Connect.

### 9. New research (`09-new-research`)

- Tabs **Company name** | **Upload a file**.
- Company tab: Company name, Website (optional), note, **Start research** (credit estimate, confirm, start).
- File tab: drop area and **Choose files** (PDF, Word, Excel, PowerPoint, CSV or text, up to 25 MB each); file list with size and status pill (Scanning, Ready, Failed); **"We found more than one company. Which is this about?"** with radio options and mention counts when the files name several companies; **Also check public sources** switch (off), with "Off by default. File-only analysis uses no research credits…"; **Estimated cost** (0 research credits when off, "About N credits, confirmed before it runs" when on); **Analyze file**; "Results are marked From your files and kept private to your workspace."
- Behaviour: quarantine, scan, parse in a sandbox, redact per policy, extract facts graded Internal with file and location; when public sources are on, run research and draw credits.

### 10. Analysis of your file (`10-file-analysis`)

- Breadcrumb, company, "Analysis of N files · <when> · no web used", pill "From your files · private to your workspace".
- `WHAT YOUR FILES SAY` narrative; sections **PLATFORMS AND PARTNERS**, **OBJECTIONS AND NEXT STEPS**, **PEOPLE AND ROLES MENTIONED** with Internal pills and file-and-page citations that open the file at that location.
- `NOT IN YOUR FILES` list as designed.
- Buttons **Ask about these files** (Ask scoped to these files), **Also check public sources** (Refresh flow with credits), **Download internal summary** (Word).
- Right rail **FILES USED** (name, pages or rows) with **Add another file**, and **PRIVACY** text with **Delete these files** (confirm dialog; deletes objects, chunks, embeddings and derived facts).

### Admin console (separate staff sign-in, dark navy sidebar)

Nav: Overview, Clients, Users, 6th Library, Runs and spend, Platform defaults; **View as a client**; signed-in admin at the bottom.

- **A1 Overview:** "Last 30 days", **Isolation check passed · <time>** pill (from the nightly isolation job), KPI cards Active clients (of onboarded), Active users (of invited), Questions asked (and % answered locally, meaning without a web refresh), Credits used (of total pooled); **Questions per week** bar chart with a client filter; **What people ask about** (question categories only, from a classifier run inside the client tenant; question text never leaves the tenant); **Not using it** (clients idle with expiry countdown) and **All users** link.
- **A2 Clients:** table Client, Users, Accounts, Credits this month (X of Y with bar, amber near the limit), Last active, Status (Active, Near credit limit, Idle, Expiring, Invited); **Add client** (name, admin email; sends nothing automatically, shows an invite link to copy).
- **A3 Client detail:** **Limits per user**: Account cap stepper (enforced at Add account), Expire access after idle days (7, 10, 14, 30; warn 3 days before), Monthly credit pool (1,000, 2,000, 3,000, 5,000). **Publish from the 6th Library**: checkbox cards of library accounts for this client. **Features**: Mail and drive connections (Allowed / Not allowed), Web refresh by users (Allowed with confirmation / Not allowed), Fastest allowed schedule. Right card **THIS CLIENT NOW** summary, warning of users near expiry, **Save changes**, **See users**, isolation status.
- **A4 Users:** User, Client, Accounts (N of cap), Questions (30d), Last active, Access pill (Active / Access ended) with **Terminate** or **Re-enable**. Terminate and expiry keep history.
- **A5 6th Library:** drop zone for account workspace zips or standing files; for each upload: Account, Deliverables found (N of 5), Internal-only file (Left out), Client-ready check (Passed / N issues, using the pre-delivery gate checks: links, banned dashes, tool names, missing files), Publish to (N clients); an issues callout per failing account; **Publish N passed accounts**.
- **A6 Runs and spend:** Credits by purpose (New account research, Scheduled refreshes, Web refreshes by users), Isolation check card, runs table: Client, Account, Type (New research, Scheduled refresh, Web refresh with scope), Credits, Started, Status (Done, Running, Waiting: credit limit, Failed).
- **A7 Platform defaults:** New client defaults (Account cap per user, Idle expiry, Monthly credit pool, Fastest scheduled refresh); Research engine (Research skill version select, Credit cap per pull, Model split: Split mode / Single model); Safeguards (Nightly isolation check: on, cannot be turned off; Client-ready check before publishing library content: on, cannot be turned off; Admin access to client questions: Off); Admins list with roles; **Save defaults**.
- **View as a client:** opens a client's workspace read-only for support, only with an access grant from that client's admin (reuse the support-grant mechanism in `api/operator.py`), never showing question text or file contents, with a visible banner and an audit log entry.

### Screens the design does not show (build them in the same style)

- Sign-in, forgot password, reset, verify email (the existing auth pages, restyled).
- A small notification list in the user shell for "refresh finished" and "waiting: credit limit".
- Empty, loading and error states for every screen.

## 3. Back end

Reuse what exists; do not delete modules this prompt brings back. `BUILD_PROMPT.md` section A5 listed them for deletion; that list is cancelled for: `wallets` and `wallet_ledger`, `runs/lifecycle.py` credit reservation, `src/metering/` and plan limits, `admin/quotas.py`, `src/spend/` (keep only what the per-pull cap needs), `src/connectors_client/` and `api/routes/connectors.py`, migration 0024, `config/billing.yaml`, `config/plans.yaml`. Verify each still works and fix what is broken.

Data model (new tables need forced RLS and entries in `tests/isolation/matrix/matrix.yaml`):

- Client settings: account cap per user, idle expiry days, monthly credit pool, fastest schedule, features (mail and drive, web refresh), and published library accounts.
- Credit pool and ledger per client per month (reserve on confirm, settle on finish, refund unused, reset on the 1st), credit cap per pull enforced by the run.
- Library: library accounts (platform-level, not tenant data), uploads, check results, publications per client; adding to a workspace copies the account into the tenant.
- Schedules per account with next run time; refresh scopes (jobs, people, full).
- Connector accounts and synced items per user (private), with match records.
- Question categories (counts only, per client, per week).
- Isolation check results (nightly job).
- User access state (active, expired, terminated) and idle tracking.

API (add every route to the catalog in `api/policy.py` with roles, audit class and tenant source; regenerate `docs/api/openapi.json` and `web/lib/gen/api.gen.ts`):

- User: `GET /v1/me` (with accounts used and cap), chat threads and streamed answers with citations and the "used" summary, `GET /v1/accounts`, `GET /v1/accounts/{id}` (narrative, findings, changes, rail counts, files), table endpoints for each deep-dive table (server-side sort, filter, paging, xlsx export), `POST /v1/accounts` (new research with credit confirm), `POST /v1/accounts/from-library`, `GET /v1/library/available`, `POST /v1/accounts/{id}/refresh` (scope, estimate, confirm), `GET /v1/credits` (pool, used, left), `GET/PUT /v1/schedules`, files upload and status and delete, file analysis, deliverables list and download (zip for several), connectors connect, callback, status, disconnect, notifications.
- Admin (staff auth plane `/operator/v1`): overview metrics, clients CRUD and settings, users access actions, library upload, check and publish, runs and spend, platform defaults, skill versions, isolation status, view-as-client with grant.

Research engine: the ABM skill executor from `BUILD_PROMPT.md` section 4, with three refresh scopes mapped to the skill's steps (jobs only, people and teams, full), the credit cap per pull stopping the run cleanly with partial results, and credits converted from the ledger's real costs through the rate table.

## 4. Demo data

Seed a local demo workspace matching the design's sample content so the replica can be compared screen by screen: client "Meridian Health Partners", user Priya Nair, six accounts (Highmark, Cambia Health, AmeriHealth, Cigna, Medica, Availity) with lanes, findings, people rows, files and schedules; a second client to prove isolation; library accounts; admin users. Seed only in development, from a script, never in production. Put demo credentials in `.env.example` placeholders, not in docs.

## 5. Quality bar

- Visual: screenshots of every screen at 1360px compared with the PDF and the design HTML, side by side, in `web/test-results/replica/` (gitignored). Fix differences.
- Accessibility (section 3 of `DESIGN_COPY_PROMPT.md`): 4.5:1 text contrast (use `--muted` instead of the failing `#98A2B3`), visible focus, 40px targets, labels, `aria-pressed` on toggles, works at 375px with an overlay menu, zero CSP console errors (no inline style attributes; self-hosted font).
- Behaviour tests (Playwright plus API tests): ask and cite; open a source; offer card to refresh; refresh with each scope reserves and settles credits; confirm disabled when credits are short; account cap blocks Add account; library publish then add to workspace; schedule change updates the estimate; scheduled run stops at "Waiting: credit limit"; file upload with two companies asks which one; file-only analysis uses 0 credits; delete files removes everything; mail and drive connect and disconnect (against a fake OAuth provider in tests); admin terminate and re-enable; idle expiry; view as client needs a grant and is audited; isolation tests for every new table and route.
- Security: tokens for mail and drive encrypted per tenant, never sent to the browser or the model; mail and drive content and file content are untrusted data, never instructions; private items never enter shareable files (leak check before release).

## 6. How to work

1. Read sections 0 and 1, then write `docs/build/REPLICA_PLAN.md`: every screen and control, the API and table behind it, what exists today, what you will build, and the order.
2. Build in this order: design system and shells; demo seed; Ask with citations and the used panel; Accounts and Account workspace; deep-dive tables; credits, pool and Refresh; Schedules and the scheduler; New research and file analysis; Add account with library; Mail and drive; admin console; View as a client.
3. After each screen, run the stack, sign in to the demo workspace, compare with the PDF page and the design HTML, and fix differences before moving on.
4. At the end report: screens done, what works end to end, what is blocked (OAuth apps, data-tool keys, the ABM skill package), test results, and every decision you need from the owner.
