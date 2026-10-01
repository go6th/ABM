# Phase 1 backlog: core pipeline, one account via API

**Status:** PROPOSED estimates for the team to agree (roadmap section 6 exit criterion: "Phase 1 backlog estimated in person-weeks and agreed").

Source: roadmap section 7 (work items, deliverables, exit criteria) and section 13 (Phase 1 is 6.5 FTE for 8 weeks, 52 person-weeks). Estimates below are planning numbers to be re-estimated with the team; they sum to the 52 person-weeks the roadmap plans.

| # | Work item | What gets built (roadmap s7) | Owner | Estimate (person-weeks) | Depends on | Phase 0 assets to build on |
|---|---|---|---|---|---|---|
| 1 | Data layer v1 | Postgres schema (accounts, runs, sources, facts, people, job postings, tech detections, coverage rows), migrations, data dictionary. Object storage for raw broker JSON with neutral names. Stable source IDs assigned once and reused. | Backend | 5 | none | `src/abm_generator/db/` (legacy schema to port deliberately), `app/brokers/base.py` neutral names |
| 2 | Broker connectors | Typed connector each for Parallel Search, Firecrawl, PredictLeads, BitScale and Sumble. Retry with backoff, response caching, pull-once guarantee, routing rules, per-call cost recorded. Test double for each broker; contract check job. | Backend | 8 | 1 | `app/brokers/`, `config/routing.yaml`, `fixtures/recorded/` |
| 3 | Workflow v1 | The 13 steps of a run (set-up, pass 1, baseline, news, jobs, tech, people, name searches, verification, distill, build, gate, delivery) as sequential Temporal stages. | Tech lead, AI | 6 | 1, 2, D1 signed | `app/workflows/`, `app/worker.py` |
| 4 | Tracker state machine | Each coverage row ends Done, Partial, Not done, Blocked or None with proof. A run cannot complete with a High row missing a status. | Backend | 3 | 1 | `config/checklist.yaml`, `app/rules/` |
| 5 | Grading engine | Schema enforces grade, URL and supporting sentence. Confirmed only if read in a primary source this run; Lead needs three queries across two tools. | AI | 4 | 1 | `config/grading.yaml`, `app/golden/schema.py` validators |
| 6 | Job posting pipeline | 12 months of open and closed postings, platform sentence read, full text for build roles, classification, counts computed in code with their base. | AI, Backend | 5 | 2, 3 | `config/models.yaml` (classification task) |
| 7 | People pipeline | Title search, two seniority passes, association search for gaps, merge by URL then name, exclusions, level, lane, priority, full profiles; alumni, tenure and certification counts in code. | AI, Backend | 5 | 2, D3 signed | `app/people.py`, `config/people_scope.yaml` |
| 8 | Export builders | Port the Node docx library and the openpyxl workbook logic to read from rows: Summary, Evidence, People, Jobs, Tech and workspace zip. | Backend | 5 | 1 | `config/output_spec.yaml`; source libraries not in this repo (see note) |
| 9 | Quality gate v1 | Port rowcheck, doc_checks, third_party_scan and strip_tool_names as blocking steps on the rendered PDF and unzipped package XML. | Backend, QA | 4 | 8 | `config/output_spec.yaml` quality_gate, `Routing.client_banned_terms()` |
| 10 | Credit cap v1 | One cap per run, per-broker spend tracking, end-of-run spend report. | Backend | 2 | 2 | `BrokerSession.spend_by_broker()` |
| 11 | API v1 | POST account, POST run, GET run status, GET artifacts; API keys; run log; thin admin console. | Backend, Frontend | 4 | 3 | none |
| 12 | Parity report | Compare pipeline output with the September manual runs on golden accounts; analyst review of every golden run. | Analyst, QA | 1 | 3 to 9, real golden accounts | `app/golden/`, `load_golden_accounts(allow_synthetic=False)` |
| | **Total** | | | **52** | | |

## Notes for agreement

- **Source libraries needed.** Items 8 and 9 port code from the current skill folder (the Node docx builder library, the openpyxl workbook logic and the four gate scripts). That folder is not in this repository. Someone with access must add it before week 3.
- **Golden accounts.** Item 12 and the Phase 1 exit criteria need three to five real, analyst-reviewed September runs in `tests/evals/golden/`. The synthetic placeholders cannot stand in for parity.
- **Live keys.** Items 2, 6 and 7 need broker keys in the staging secrets manager (`app/settings.py` `LIVE_KEYS`).
- **Risk from the roadmap.** Over-engineering the schema early: keep v1 narrow and add columns through migrations.

## Phase 1 exit criteria (for reference)

- Three golden accounts run through the API with no manual steps other than agreeing the cap.
- At least 90 percent of High priority rows reach the same or a better status than the manual run (target to validate).
- Zero quality gate failures on delivered files; every graded row carries a working link.
- Spend report within the agreed cap; no source pulled twice.

## Agreement

| Role | Name | Date | Agreed |
|---|---|---|---|
| Tech lead | | | |
| Product owner | | | |
