"""Part 1: the big picture (chapters 1-5)."""
from kit import *
import figs_core as FC
import figs_more as FM


def build():
    h = []
    h.append(part("Part 1  The big picture", "What this website is, who it is for, how it was built, and what it is made of."))

    # ---------------------------------------------------------------- 1
    h.append(chapter("1", "What this project is, in plain words",
                     "A sales-research website. A salesperson types a company name; the system reads the internet and the salesperson's own files, checks every fact, and hands back finished Word and Excel files."))
    h.append(p("The product is called **Account Intelligence**. The folder on disk is called **ABM Generator**; the company behind it is called **6th**. ABM means *account-based marketing*: selling to a chosen list of companies instead of to everybody. For each company on the list a seller needs to know who the key people are, what technology the company uses, what jobs it is hiring for, what is in the news, and what to say in a first call. Doing that by hand takes hours. This system does it in the background."))
    h.append(h2("What a customer sees"))
    h.append(ol([
        "They sign in (company single sign-on or a password) to a workspace that belongs only to their company.",
        "On the home screen, **Ask**, they can question everything the system knows. Answers cite numbered sources.",
        "They click **Add account**, type a company (or upload their own files such as spreadsheets and call notes) and see an estimate of what the research will cost. They press Start.",
        "A **Research board** shows the work running live. It can take hours, because it reads hundreds of pages.",
        "When it finishes they get an **Account Summary** (Word), a **Sources and Evidence** document (Word), three Excel workbooks (People, Job Postings, Tech Stack and Teams) and a zip with everything. The same tables are also shown on screen.",
        "They can schedule a refresh (every 3 days, weekly, every 2 weeks, monthly) and ask more questions at any time.",
    ]))
    h.append(h2("What makes it different from a chatbot"))
    h.append(p("Most AI tools simply write what sounds right. This one is built around **not trusting the AI**. The AI is only allowed to *propose* a fact together with a sentence copied word for word from a web page. Plain code then checks that the sentence really is on that page, works out how reliable the source is, and decides the fact's **grade** (Confirmed, Probable, Hypothesis, Lead). The AI cannot write a grade, because the data format it must answer in has no field for one. Only Confirmed facts may be stated to a prospect as fact."))
    h.append(h2("Who the system serves"))
    h.append(table(["Group", "What they do", "Where"],
        [["**Customers** (tenants)", "Sales teams that research companies. Each customer is sealed off from every other customer, enforced inside the database.", "The main website"],
         ["**Roles inside a customer**", "admin, analyst, seller, viewer. Admins invite teammates, grant staff support access, manage people-data consent.", "Team page, API role checks"],
         ["**6th staff** (operators)", "Add customers, set limits, suspend, publish the shared Library, see cost and runs. Separate sign-in (Google Workspace) and a separate set of API routes.", "/operator console"]],
        [24, 52, 24]))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 2
    h.append(chapter("2", "The one-page verdict",
                     "Read this page first. Everything else in the PDF backs these statements up."))
    h.append(box("What is good", ul([
        "The design is unusually careful for a vibe-coded project: separate customers are isolated by the **database itself** (row-level security), not only by application code, and there is a nightly self-test of that.",
        "Every route on the server must be listed in a catalogue or it answers 403 (default deny). 219 routes match the catalogue exactly.",
        "Money is handled with care: every paid outside call is written to an insert-only ledger **before** it is made and replayed afterwards, so a crash does not make you pay twice.",
        "A lot of automated checking exists: about 4,260 tests, import-layer rules, route-catalogue checks, migration lints and generated-file drift checks.",
        "The AI is boxed in: strict output formats, quotes verified word for word, grades computed by code, judges that can only lower a grade.",
    ]), "good"))
    h.append(box("What is risky", ul([
        "**All spending caps are switched off on staging** (`RESEARCH_CAPS=off`), and so are the token guard and the dollar cap. The only hard brakes left are \"three empty slices in a row\" and per-call time limits. The owner's own notes say ~$500 was lost to failed runs earlier.",
        "**Documentation disagrees with the code** in at least a dozen places (section 23). The guide in `docs/guide` was written on 29 October; the code has changed since.",
        "**Large parts are dead or only half-wired**: the whole monitoring/alerts product, an alternative model router, four \"security/metrics/registers/unitcost\" packages that do nothing (one of them always says \"all clear\"), 18 redirect-only web pages, 73 API routes that nothing calls.",
        "**A real company's workspace file is committed in git history** (`GSK_workspace.zip` under `unwanted-things/`). If the GitHub repository is not strictly private, that is a data leak.",
        "**Releases are manual** (Cloud Build and gcloud). The automatic deploy workflows exist but may never have run. Nothing in the repo applies Terraform automatically.",
        "The worker process has been killed repeatedly by slow or blocked code, and the single-core database has saturated once under load.",
    ]), "risk"))
    h.append(box("What to do first", ol([
        "Confirm the GitHub repository is private; plan a history rewrite to remove the real-account zip.",
        "Decide whether the spending caps should stay off. If staging is used by anyone other than you, turn `RESEARCH_CAPS` back on.",
        "Never start a research run or run the `eval`/`live` tests without understanding they spend real money (section 26).",
        "Pick one source of truth for documentation and refresh it (this PDF can be that starting point).",
        "Archive or delete the dead code listed in section 24 so the next reader is not misled.",
    ]), "todo"))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 3
    h.append(chapter("3", "How it was built, from zero to a running product",
                     "Everything here is inferred from the git history, branch names, comments and documents. It is the honest reading of the evidence, not the builder's own account."))
    h.append(fig(FM.fig_timeline(), "Commits per day. One person committed all 580, in 34 days. The tall bars match big feature pushes.", wide=True))
    h.append(table(["Fact", "Value", "Where it comes from"],
        [["Commits", "580, one author (Sakib-11)", "git log"],
         ["Dates", "1 Oct 2026 to 3 Nov 2026 (34 days)", "git log"],
         ["Python code in `src/`", "about 161,000 lines", "line count"],
         ["Python tests", "about 120,000 lines, 501 files, ~4,260 test functions", "line count"],
         ["Website (`web/`)", "about 24,000 lines of TypeScript", "line count"],
         ["Database migrations", "96 files, about 12,600 lines of SQL", "`migrations/`"],
         ["Cloud definition (Terraform)", "about 4,400 lines", "`infra/`"],
         ["Rules as data", "about 75 YAML files in `config/` (5,700 lines)", "`config/`"],
         ["Documents", "354 Markdown files, 98 recorded design decisions (ADRs), a 101 KB BUILD_LOG", "`docs/`"],
         ["Repository size", "5.2 GB on disk (mostly caches); `.git` is 371 MB", "disk"],
         ["Branches", "14 local, with agent-style names (`agent/*`, `claude/*`, `codex/*`)", "git branch"]],
        [26, 50, 24]))
    h.append(h2("The story in four acts"))
    h.append(table(["When", "What was built", "Evidence"],
        [["**1-4 Oct**", "A scaffold: folder layout, tooling, CI, Docker; the AI-skill files; written specs for five data providers; the grading design as numbered decision records (ADRs 0010-0014); the first workflow that builds exports.", "commit messages \"scaffold Phase 0 repo\", \"docs(providers)\", \"docs(grading)\""],
         ["**6-9 Oct**", "The spending and credit manager, database migrations for views and exports, the CRM write-back ledger, the website shell, login and dashboards, a Claude-style chat interface with automatic model routing.", "\"credit manager v2\", \"migration 0023\", \"feat(phase-6): Claude-style chat\""],
         ["**14-17 Oct**", "The biggest day (112 commits): tests for credits and research jobs, the **replica** product (the real customer screens), cross-tenant database functions owned by no-login roles, uploading zips and folders, Ask answers that render Markdown. A dead-code audit.", "\"docs(deploy): dead-code audit\", \"fix(db): cross-tenant definer functions (M1)\""],
         ["**20 Oct - 3 Nov**", "Production-hardening in small steps: fixes for runs that died, speed work, removal of caps, a workspace repair stage for \"Download everything\", purging one customer's data from staging, making Ask read whole files, memory, own knowledge and web.", "\"Make Ask reason like Claude with the workspace\" (3 Nov), \"Fix the errors found by the Ask audit\""]],
        [14, 58, 28]))
    h.append(h2("How the author seems to have worked"))
    h.append(ul([
        "**AI-assisted from end to end.** Branch names such as `agent/backend-hardening`, `claude/...` and `codex/...`, very long explanatory comments, and phrases like \"owner decision 2026-10-21\" show a person directing AI coding agents and recording their own decisions.",
        "**Rules written down as code.** Many rules carry a `GUARANTEE(test_name)` marker. A script fails the build if the named test does not exist. Every loop must declare a `LOOP(...)` cap. Every public function needs a docstring (`interrogate` at 100 percent).",
        "**One human, no review trail.** There is no pull-request review history in the repository. Knowledge of why things are the way they are lives in the ADRs, the comments, and the owner's notes.",
        "**Cleanup by moving, not deleting.** A folder called `unwanted-things/` holds files that were taken out of the live tree but never deleted (504 MB, including a 485 MB old build).",
    ]))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 4
    h.append(chapter("4", "What it is made of: languages, libraries and services",
                     "Every library that the project declares, grouped by job. Versions are from the dependency files; \"used for\" was checked against the code."))
    h.append(h2("Languages and tooling"))
    h.append(table(["Layer", "Choice", "What that means for you"],
        [["Server language", "Python 3.12 (3.11 to 3.12 allowed)", "All logic of the platform. Managed with **uv** (a fast package tool). Version lock is `uv.lock`."],
         ["Website language", "TypeScript 6 with React 19.3 and **Next.js 16.4.1** (App Router)", "The screens. Runs as its own Node 22 service."],
         ["Database", "PostgreSQL 16 plus the **pgvector** extension", "All data, including search vectors. ~205 tables."],
         ["Cloud", "Google Cloud (Cloud Run, Cloud SQL, Cloud Tasks, Cloud Scheduler, Cloud Storage, Secret Manager, KMS, Memorystore Redis)", "Everything runs on Google. The laptop only holds the code and a fake local stack."],
         ["Infrastructure as code", "Terraform 1.9+", "The cloud is described in files under `infra/`."],
         ["Containers", "Docker, three images: api, worker, web", "One Python image serves both api and worker."]],
        [20, 38, 42]))
    h.append(h2("Python libraries (server)"))
    h.append(table(["Library", "Used for"],
        [["**fastapi**, **uvicorn**", "The web server that answers every `/v1/...` call and the worker's `/tasks/...` calls."],
         ["**pydantic**, **pydantic-settings**", "Describes and validates every request, response and environment setting (about 170 settings, checked at start-up)."],
         ["**sqlalchemy** (asyncio), **asyncpg**, **psycopg**", "Talking to PostgreSQL. Almost all queries are hand-written SQL; only 5 ORM models exist."],
         ["**alembic**", "Runs the numbered database migrations (each is a plain .sql file wrapped by a small Python file)."],
         ["**cloud-sql-python-connector**", "Connects to Cloud SQL with Google identity instead of a stored password."],
         ["**langgraph**, **langgraph-checkpoint-postgres**", "The research graph and its saved checkpoints in Postgres, so a crashed run resumes."],
         ["**anthropic**", "Calls Claude (directly or through Vertex AI). Kimi and Grok are called with plain HTTP."],
         ["**httpx**, **httpcore**", "All outgoing web calls (data providers, Google, the AI)."],
         ["**redis**", "Rate limits and a cache index. Holds nothing authoritative."],
         ["**google-cloud-storage / tasks / kms / secret-manager / monitoring**, **google-auth**", "Files, work queue, encryption keys, secrets, metrics, and verifying Google-signed calls."],
         ["**opentelemetry-*** ", "Traces and metrics into Cloud Monitoring and Cloud Trace."],
         ["**pyjwt**, **signxml**, **argon2-cffi**", "Verifying sign-in tokens, verifying SAML single-sign-on signatures, hashing local passwords (Argon2id)."],
         ["**python-docx**, **openpyxl**, **pypdf**, **pyarrow**, **pillow**", "Writing the Word and Excel files; reading uploaded PDFs, spreadsheets, Parquet and images."],
         ["**jsonschema**, **pyyaml**, **python-multipart**", "Validating events, reading all the `config/*.yaml` rules, SAML form posts."],
         ["**fastembed** (cpu) or **fastembed-gpu** + **onnxruntime-gpu** (gpu)", "Embedding model `bge-small` that runs on the server to turn uploaded text into search vectors."],
         ["**pandas**", "Declared but **never imported** in `src/` or `scripts/`. Probably removable."]],
        [38, 62]))
    h.append(h2("Website libraries"))
    h.append(table(["Library", "Used for"],
        [["**next** 16.4.1, **react** and **react-dom** 19.3", "The framework and UI. Every page is rendered per request (needed for the security nonce)."],
         ["**zod** 4.6.7", "Checks every request and response against schemas generated from the API's OpenAPI file."],
         ["**react-markdown**, **remark-gfm**, **rehype-sanitize**", "Shows the Markdown answers from Ask, with tables, and strips anything unsafe. All three exist for this one feature."],
         ["**@radix-ui/react-dialog**", "Two dialogs in the staff console only."],
         ["**lucide-react**", "One icon in one file. Removable."],
         ["vitest, testing-library, jsdom, Playwright, axe-core", "Unit tests, component tests, end-to-end and accessibility tests."],
         ["eslint, typescript", "Linting and type checking. No Tailwind: styling is about 1,500 lines of hand-written CSS using design tokens."]],
        [38, 62]))
    h.append(h2("Outside services the product talks to"))
    h.append(table(["Service", "Real name", "Used for"],
        [["`p_search`", "Parallel (parallel.ai)", "Web search and reading known pages; also the web lookups inside Ask."],
         ["`p_scrape`", "Firecrawl", "Scraping careers and job-board pages."],
         ["`p_signals`", "PredictLeads", "News events, a 12-month feed of job openings, technology detections."],
         ["`p_people`", "BitScale", "People by job title, profile enrichment."],
         ["`p_org`", "Sumble", "Company matching, technology stack with teams, people by technology."],
         ["AI models", "Anthropic Claude (Opus 5.5, Fable 5.1, Sonnet 5.5, Haiku 5.5); optional Kimi K3 (Moonshot or Ollama Cloud) and Grok 4.5 (xAI)", "Reading, planning, judging, writing, Ask."],
         ["Identity", "Google Identity Platform (customers' tokens), Google Workspace (staff), customer SSO (OIDC and SAML)", "Sign-in."],
         ["Mail and drive", "Gmail, Google Drive, Microsoft Graph (optional)", "A user's own read-only mail and files."],
         ["Mail out", "Any SMTP server", "Verification and reset emails."]],
        [14, 40, 46]))
    h.append(callout("Why the provider names are hidden", "In the code and in client files the data providers are called `p_search`, `p_scrape`, `p_signals`, `p_people`, `p_org`, and client files say \"6th agents\". The real vendor names live only in `config/providers.yaml`, on purpose: a deny-list scanner stops any vendor name from reaching a customer file.", "note"))
    h.append(end_chapter())

    # ---------------------------------------------------------------- 5
    h.append(chapter("5", "The system as one picture",
                     "One code base is built into three running services plus a small migration job. Figure 2 shows how they and the cloud services connect."))
    h.append(fig(FC.fig_architecture(), "Architecture on Google Cloud. The browser only talks to the web service. The worker does the slow work and is woken by Cloud Tasks and Cloud Scheduler.", wide=True))
    h.append(table(["Service", "Resources (staging)", "Job"],
        [["**web**", "1 vCPU, 512 MiB, behind the load balancer", "Serves the screens. Proxies every `/v1/*` call to the api, adding cookies and security headers."],
         ["**api**", "1 vCPU, 2 GiB, 0 to 3 instances, ingress internal only", "Checks who you are, reads and writes data, starts research, answers Ask, hands out files."],
         ["**worker**", "4 vCPU, 12 GiB, 0 to 2 instances, always-on CPU, a ClamAV virus-scanner sidecar, optional GPU", "Runs research in slices, reads uploaded files, runs the 5-minute sweeper and scheduled jobs."],
         ["**migrate job**", "Cloud Run job", "Updates the database layout before each release."]],
        [14, 36, 50]))
    h.append(p("The api and the worker run the **same program** (`uvicorn account_intelligence.api.main:app`), so every route exists on both. What differs is who may reach them and which secrets are mounted. Only the worker runs the sweeper loop."))
    h.append(h2("Words you will meet"))
    h.append(table(["Word", "Meaning"],
        [["**Tenant**", "One customer company. Their data is invisible to all others."],
         ["**Account**", "A target company being researched, such as `bd.com`."],
         ["**Job**", "The customer's request \"research this account\" (table `research_jobs`)."],
         ["**Run**", "One attempt to carry out a job (table `account_runs`). Resume creates a new run."],
         ["**Slice**", "A run is cut into pieces of about 27.5 minutes (1,650 seconds) so no single request lives too long. Progress is saved between slices."],
         ["**Checkpoint**", "The saved state of the research graph after every step; lives in Postgres."],
         ["**Ledger**", "The insert-only record of every paid outside call and its cost. A call already paid for is replayed, never paid again."],
         ["**Fact / grade**", "One thing learned about a company with its source. Grades: Confirmed, Probable, Hypothesis, Lead, Internal Only."],
         ["**Gap**", "A checklist row the research could not fill. It is shown honestly, with a reason."],
         ["**Credits**", "The customer's allowance. 1 credit = $0.05. The screens hide credits; the ledger still records them."],
         ["**Caps**", "Limits on spending, tool calls and time. Currently off on staging."],
         ["**Hermetic**", "A fully fake mode (fake model, fake providers) for tests and demos. Nothing paid can happen."],
         ["**RLS**", "Row-level security: PostgreSQL itself hides other customers' rows."]],
        [20, 80]))
    h.append(end_chapter())
    return "\n".join(h)
