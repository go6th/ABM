"""Part 5: findings and handover (chapters 22-26)."""
from kit import *


def build():
    h = []
    h.append(part("Part 5  Findings and handover", "What is wrong, what is out of date, what is dead, what could not be checked, and a safe way to take over."))

    h.append(chapter("22", "Findings, ranked",
                     "Ranked by how likely each is to hurt a new owner. Evidence is from reading code; nothing was run."))
    rows = [
        [sev("High"), "**Every spending control is off on staging.** `RESEARCH_CAPS` defaults to `off`: no dollar cap, no repeat-call limit, no per-user research limit, no credit-pool gate, slice ceiling of 1,000,000, no per-agent token budget, token guard silent. Only three-empty-slices and per-call time limits remain. A runaway run is limited by the clock and whoever notices.", "`infra/envs/staging/main.tf:64`, `container.py:452`, `spend_guard.py:168`", "Decide deliberately. If anyone else uses staging, set `RESEARCH_CAPS=on` and `TOKEN_GUARD=on`."],
        [sev("High"), "**Credits settle at the smaller of real spend and the reservation**, and the reservation comes from placeholder numbers in `credits.yaml`. With caps off the platform pays the difference. The nightly invariant job is the only alarm.", "`replica/credits.py:408`, `config/credits.yaml:1-8`", "Add a real spend alarm; replace placeholders with measured costs."],
        [sev("High"), "**A security package that always says all clear** (and three more no-op packages).", "`src/security/*`", "Delete or implement; remove from the wheel and coverage."],
        [sev("High"), "**A real company's workspace zip is tracked in git history.**", "`unwanted-things/real-account-fixtures/.../GSK_workspace.zip`", "Confirm the remote is private; rewrite history; rotate if shared."],
        [sev("Medium"), "**Replay keys are fragile.** Checkpoints are per node; a killed `pull`/`interpret`/`pipelines` node re-runs whole and relies on exactly keyed replays. Changing a prompt, a query template, `pull_plan.yaml` or URL canonicalisation silently invalidates replays and re-pays.", "`agents/graph.py`, `connectors/ledger.py`, `llm/reply_cache.py`", "Treat these files like a data migration; add a test that fails when keys change."],
        [sev("Medium"), "**Database hot path on a single-core database.** Every paid call does a ledger-claim transaction that also renews the run lease. With wide fan-out and caps off this is a plausible cause of the 1 Nov saturation. Pool is 5 plus 5 per process.", "`connectors/ledger.py:281-420`, `infra/stack/main.tf:229`", "Bound fan-out; size the database; add a DB saturation alert."],
        [sev("Medium"), "**Event-loop stalls have killed the worker at least four times** (YAML parse, certificate fetch, archive zip, ledger parse run on the loop). The stall dump is a diagnostic, not a cure. Worker concurrency 4 means one stuck request shares the process with three more.", "`obs/stalldump.py`, docs/guide 06", "Move blocking work to threads; reduce concurrency to 1 while debugging."],
        [sev("Medium"), "**The monitoring product is not connected**: `refresh`, `signals`, `alerts` run only in tests. Two scheduler entries are paused and point at missing routes.", "`refresh/probe.py:149`, `infra/stack/main.tf:768`", "Wire it or remove it; do not demo \"alerts\" as a feature."],
        [sev("Medium"), "**Two completeness rules**: the graph's `deliver` node and `lifecycle.finish` decide Completed versus Partial separately and can disagree.", "`agents/graph.py:1505`, `runs/lifecycle.py:748`", "Make one the single owner."],
        [sev("Medium"), "**Model routing is doubled and mostly dormant.** The Router is off; Opus (not Fable) writes the Summary; `models.yaml` and `model_routing.yaml` both hold model ids; two price tables must stay in sync.", "`workflow/deps.py:122`, `config/models.yaml`", "Delete one system; fix the docs."],
        [sev("Medium"), "**Judge independence fails soft**: if judge and extractor profiles ever matched, claims needing a judge would be silently capped to Probable.", "`grading/integration.py:85`", "Make it raise."],
        [sev("Medium"), "**The quality gate is partly honour-system**: it trusts the builder by SHA-256 and does not re-run format rules; LibreOffice render checks log `not_run` if the image lacks it.", "`documents/gate.py:147`, `exports/builder.py:1254`", "Confirm LibreOffice is in the worker image."],
        [sev("Medium"), "**No bound on scheduled refresh starts** per 15-minute tick.", "`replica/schedules.py:148`", "Add a per-tick ceiling."],
        [sev("Medium"), "**Manual and partly undocumented operations**: Cloud Build and gcloud releases, manual Terraform apply, a stale deploy script that prints asia-south1 commands, a hand-made purge job, paused retention.", "`DEPLOYMENT.md`, `scripts/deploy_staging.sh`", "Write the real runbook; make one path the routine."],
        [sev("Medium"), "**Local work is ahead of the remote**: 2 commits ahead of `origin/feat/final-product` and 9 ahead of `origin/main`.", "git status", "Push to a safe place."],
        [sev("Low"), "**Large dead surface**: 8 packages with no importer, 5 unused task kinds, 73 uncalled API routes, 18 web stubs, unused YAML files.", "see chapter 24", "Archive in one pass."],
        [sev("Low"), "**A garbled prompt**: the injection-guard sentence of `posting_second_pass.v1.md` lost its key words.", "`agents/prompts/posting_second_pass.v1.md:1-2`", "Restore from `posting_classifier.v1.md`."],
        [sev("Low"), "**No payment processor** exists; `billing.yaml` describes a rule the code does not follow.", "`config/billing.yaml`", "Mark as notes."],
        [sev("Low"), "**SAML sign-in through the web proxy is probably blocked** by the same-origin check.", "`web/app/v1/[[...path]]/route.ts`", "Test with a real SAML provider."],
        [sev("Low"), "**One author, no review trail**: knowledge lives in comments, ADRs and the owner's notes.", "git log", "Record the open questions below."],
    ]
    h.append(table(["Severity", "Finding", "Evidence", "Suggested action"], rows, [10, 46, 22, 22], "sm"))
    h.append(end_chapter())

    h.append(chapter("23", "Where the documentation is wrong or stale",
                     "The old guide in `docs/guide` is right about structure but is a 29 October snapshot. These are the concrete errors found."))
    h.append(table(["Document says", "Code says"],
        [["`max_slices` is 8 (guide 02)", "16 in `config/workflow_v1.yaml`; effectively unlimited on staging."],
         ["Token guard stays on with caps off (guide 02)", "Off unless `TOKEN_GUARD=on`; no infra file sets it. Guide 04 has it right."],
         ["The graph's `finish` node settles credits", "It moves no money; the runner settles through `lifecycle.finish`, `settle_once`, `credits.settle_run`."],
         ["Entity lock stops the run before any money is spent", "It makes two small paid provider calls first, then refunds on a mismatch."],
         ["The sweeper does five things", "Six: \"close ended jobs\" is missing."],
         ["Full-research credit floor 110 (guide 06)", "`credits.yaml` is now 250, so the floor is 125."],
         ["Migrations run to 0085 and are \"SQL steps\" (guides 01, 03)", "Head is 0097; they are Alembic Python files wrapping SQL. `migrations/README.md` names 0095."],
         ["`src/llm/port.py` is the only door to a model (guide 03)", "It runs only with `MODEL_ROUTER=on`; the live path is `platform/llm.py`."],
         ["Fable writes the Summary (config comments)", "sense_check and distill are on the planner tier, so Opus does."],
         ["The skill is injected into every research model call", "Only the six `abm_*` task kinds and Ask."],
         ["Ask never reaches the web (docstrings)", "Web tools exist; `ask_web` defaults to on, gated by a provider key."],
         ["The monitoring engine is live (guide 03)", "Not wired to anything."],
         ["`web/README.md`, `docs/frontend/*` describe settings, runs, alerts, usage screens", "Those screens were removed; only redirects remain."],
         ["`make deploy-staging` prints deploy commands", "They are stale (asia-south1, one image); the live setup is us-central1 with three images."],
         ["`DEPLOYMENT.md`: routine release is push to main", "Releases appear to be done by hand."],
         ["`docs/guide/06` says nothing is committed and the worker is revision 00077", "The tree is committed, with at least 16 commits since 28 Oct."],
         ["`RUNBOOK.md`: Python 3.11+", "Images and CI use 3.12; mypy needs 3.12."],
         ["`pyproject.toml`: coverage `fail_under = 94`", "The comment says it predates five packages and was never re-measured."]],
        [46, 54], "sm"))
    h.append(end_chapter())

    h.append(chapter("24", "Dead, dormant and unfinished code",
                     "Code that exists but does nothing, or that nothing reaches. Useful to know before you spend time reading it."))
    h.append(table(["What", "Status"],
        [["`security`, `metrics`, `registers`, `unitcost`", "Stubs of 13-33 lines; imported only by tests."],
         ["`privacy`, `quality`, `contracts`, `evalgate`", "No importer in `src/` (runtime use unverified)."],
         ["`refresh`, `signals`, `alerts` (monitoring)", "Built and tested, not wired; `run_probe` called only by tests."],
         ["`crm_writeback`", "Dormant until a CRM app is registered."],
         ["`llm/` Router, `model_routing.py`, the `routing:` block of `models.yaml`", "Dormant or dead."],
         ["`rag/graph.py`, `rag/prompt.py`", "No production importer."],
         ["`verify/narrative.py` and its prompt", "No production caller."],
         ["Five task kinds", "Registered, never called."],
         ["`config/lessons.yaml`", "Empty (`lessons: []`)."],
         ["Cloud Scheduler `retention` and `alert-digest`", "Paused; their routes do not exist."],
         ["Seven YAML files", "`claims`, `messaging_rules`, `pricing`, `secrets_rotation`, `soc2_controls`, `risk_register`, `data_model_spec`: notes, no code reads them."],
         ["Web: 18 redirect pages, `lib/query.ts`, `dev.tsx` config, `lucide-react`, `@vitest/coverage-v8`", "Removable."],
         ["API: 73 routes with no web caller", "Dormant features."],
         ["Python: `pandas`", "Declared, never imported."],
         ["`replica/jobs.py` strings \"executor not built\"", "Misleading: the graph engine is the live one."],
         ["`unwanted-things/` (504 MB)", "Parked files; includes the real-account zip."]],
        [44, 56], "sm"))
    h.append(end_chapter())

    h.append(chapter("25", "What this audit could not verify",
                     "Be honest about the edges. Nothing was run, deployed or connected during this audit."))
    h.append(ul([
        "**The live cloud state**: which revision is deployed and the real values of `RESEARCH_CAPS`, `TOKEN_GUARD`, `MODEL_ROUTER`, `ASK_WEB`. The Terraform defaults were read; the private tfvars file was deliberately not opened.",
        "**Whether the GitHub repository is private**, and whether the GitHub Actions deploys ever ran.",
        "**Test results**: 4,260 test functions exist; none were run.",
        "**Runtime behaviour** of any route, provider adapter, scraper or the web screens.",
        "**Whether LibreOffice is in the worker image**, the exact Cloud SQL tier, Cloud Armor rate-limit values.",
        "**Real accessibility**: the code does the right things, but no screen reader was used.",
        "**The prompts' exact assembled size** (the skill context is estimated at 45-50 KB per call).",
        "The agents that produced the detailed inventories worked by reading code; where they could not confirm a statement they marked it \"unverified\" and this PDF keeps that word.",
    ]))
    h.append(end_chapter())

    h.append(chapter("26", "Taking over safely: a first-week guide",
                     "Practical steps, ordered so nothing costs money or breaks anything before you understand it."))
    h.append(h2("Do not do these"))
    h.append(box("Costly or destructive", ul([
        "**Do not start a live research run** (the live compose file or a deployed staging account) to \"try it\". A run reads hundreds of pages and calls the paid AI; earlier failed runs cost about $500.",
        "**Do not run tests with `-m eval` or `-m live`.** They call the real paid model. Always use `-m \"not eval and not live\"`.",
        "**Do not run two test sessions against the same scratch database**: the test fixture drops and recreates it.",
        "**Do not `source .env`** before testing; its `DATABASE_URL` hijacks the test database.",
        "**Do not apply an old Terraform plan.** Always make a new plan first.",
        "**Do not run `unwanted-things/cleanup.sh`** until you have read it; it holds the irreversible steps.",
    ]), "risk"))
    h.append(h2("Week-one plan"))
    h.append(table(["Day", "Do this"],
        [["1", "Read chapters 1-5 and 22. Confirm the GitHub repository is private. Make a copy of the whole folder and of `.git`."],
         ["2", "Run the **hermetic** stack: `make up` then `make seed`. Sign in as the local test user and click through every screen (the seed script may ask for a DEMO_PASSWORD setting). Nothing paid can happen in this mode."],
         ["3", "Run `make check` against a private scratch database. Read the output; it shows the rules the builder enforced."],
         ["4", "Open the cloud console (read-only): Cloud Run services, Cloud Tasks queues, the Scheduler list, the billing budget. Compare with chapter 19."],
         ["5", "Decide the cap question (chapter 22, finding 1) and the dead-code cleanup (chapter 24). Write the answers down."]],
        [8, 92]))
    h.append(h2("Where to look when something breaks"))
    h.append(table(["Symptom", "Look at"],
        [["A research ended early", "The run's `stop_detail`; the reason code table in chapter 6; `workflow/runner.py` (`_timed_out`, `_end_no_progress`, `_stop_unexpected`)."],
         ["A run failed with no clear error", "The **LangGraph checkpoint tasks** in Postgres, where the real error is stored; the `run.finished` log event."],
         ["Costs look wrong", "`tool_calls` and `model_reply_log` tables; `scripts/cache_report.py`; `scripts/token_savings.py`."],
         ["A file was held back", "`run_gate_results` and the `files_withheld` / `build_failed:*` reason codes."],
         ["A customer sees another's data", "Stop everything. `tests/isolation/`, `config/ownership.yaml`, the nightly `isolation-check` result."],
         ["The page for an account hangs", "A heavy `NEWEST_FACT` query on a one-core database (fixed once in commit 2b697e79)."]],
        [30, 70]))
    h.append(h2("Questions to put to the person who built it"))
    h.append(ol([
        "Why are the caps off, for how long, and who else uses staging?",
        "Is the GitHub repository `go6th/ABM` private, and who has access?",
        "Which of the dormant systems (monitoring, Router, CRM write-back, RAG) are meant to ship and which are experiments?",
        "What produced the `credits.yaml` numbers, and what does a typical full research actually cost?",
        "What is the `ai-staging-purge` job and why is it not in Terraform?",
        "Do the GitHub Actions deploys work? Which path do you use for releases?",
        "What are the real production plans: customers, data residency, region, a payment processor?",
        "Where are the provider keys and who owns each provider account?",
        "Which decisions in the ADRs and `BUILD_LOG.md` are still open?",
    ]))
    h.append(end_chapter())
    return "\n".join(h)
