# Prompt: build the Job posting pipeline (12 months of postings, platform sentence, full text for build roles, four-way classification, counts with their base), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **Job posting pipeline**, the engine behind PRD step 5 (Jobs): a live board search per lane keyword, then a **12-month pull of open and closed postings**, the **platform sentence read in every posting that names a platform**, the **full text read for build roles**, each posting **classified as Build role, Admin and owner role, Working tool or User role**, and **counts computed in code with their base**. This is backend work. Write production-grade code and prove it with a real harness. Never invent facts about the process, the providers or the account, and never let a model or a caller type a count or a class without a verified reading.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/01-product-and-process.md` (step 5, research rules), `references/04-run-lifecycle-and-agents.md`, `references/05-data-model.md` (`job_postings`), `references/06-evidence-and-grading.md` (counts name their base, keyword match is a Lead), `references/03-tenancy-isolation-security.md`, `references/12-open-decisions-and-verification.md`, `references/13-starter-code-and-known-defects.md`, `assets/checklist.yaml`, `assets/routing.yaml`, `assets/alert_rules.yaml`, `assets/client_output_denylist.txt`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD, read sections 3.4 (research coverage and rules), 4.2 step 5 and Appendix A and cite them. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Fixed by the skill:** the database enforces isolation (`tenant_id NOT NULL`, forced RLS with `WITH CHECK`, roles `TO app_rw`, access through `tenant_txn`); pull once and work from stored payloads; one retry owner per failure class (the node `RetryPolicy`; this pipeline adds none); untrusted text reaches only the tool-less extractor; no fact without URL and evidence; client-facing output never names brokers or shows credit counts; nothing is deleted or overwritten silently.
3. This pipeline owns the coverage rows `job_board_by_lane`, `job_postings_12_months` and `job_postings_read_classified` (confirm against `config/workflow_v1.yaml` if it exists). It **never writes a row status**; it hands evidence to the tracker (`record_outcome`) or, if the tracker does not exist yet, to the `CoverageRepo` port.
4. No real provider calls, no real LLM calls, no real spend, no real tenant data. Fakes and recorded fixtures only.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/job-posting-pipeline` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

Print a table of what exists: the connector layer (`call_operation`, the job-board, job-feed and page-read operations, normalised posting records, pagination caps), the payload store, `job_postings` and any posting tables with their exact columns, `PostingRepo`, the tracker (`record_outcome`), the grading engine (`ProposedClaim`, `grade_claims`, `spans.py`), the workflow stage `s05_jobs`, the extractor and judge ports, `tenant_txn`, the isolation suite.
- Missing dependency: define a typed `Protocol` in `src/jobs/ports.py` and an in-memory fake in `tests/fakes/`. Ports: `PostingSourcePort` (live board search and 12-month pull, returning normalised `RawPosting` records with provenance), `PageReadPort`, `PayloadStore`, `PostingRepo`, `CoverageEvidencePort`, `GradingPort`, `ExtractorPort`, `JudgePort`, `RunInputPort` (lanes, verified careers URL, `as_of` date), `Clock`. Production adapters that do not exist raise `NotConfigured`; the rows then end Not done with reason `not_available_yet`, never a guess.
- **One implementation per rule.** If quote, span or Lead logic exists in the grading engine, reuse it (`spans.verify_span`); do not copy it. If the workflow already contains posting logic, move it here and leave a thin re-export.
- If a `job_postings` table exists, **extend it expand-only** (add columns, constraints, indexes; never drop or alter an existing column; ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. The step wording, the four class names, the lane names, the 12-month window, the counts rule and the coverage rows come only from the sources. **The sources do not define the four classes, the lane keywords or any platform alias.** Everything below is configuration with a `source` field: where the sources do not say, it is `source: TODO, needs product owner`; where I propose a default, it is `status: proposal`. Do not write title lists, phrase lists, aliases or definitions from memory into code.
2. **Platform lexicon:** seed `config/job_lanes.yaml` only with the lane and platform names the PRD actually names (Salesforce including field service such as ServiceMax, ServiceNow, SAP, Tableau and analytics, Veeva, Data and AI). Aliases and lane keywords are empty and marked TODO. A test fails if an entry has no `source`.
3. **Classification rubric:** `config/posting_rubric.yaml` holds the class definitions and the deterministic cues. Seed it with a **short list of unambiguous cues** derived from the class names themselves, every rule with a `rule_id`, `status: proposal`, and a positive and a negative test example. The product owner extends it. Record the definitions as **D-16** in the open-decision register and an ADR.
4. **Proposals of mine, not facts from the sources** (config marked `status: proposal`, ADR, register entries at the next free numbers; the earlier prompts used D-12 to D-15, so start at **D-16** unless the register shows otherwise):
   - **D-16 classification and full-text scope:** the class definitions and cues; **title-only matches are not classified** (a title match is a Lead until the description names the platform); the full-text scope. The PRD says "full text read for every role where the platform is the job" in the rules and "full text for every build role" in step 5; the roadmap says "build roles". Default `full_text_required_classes: [build, admin_owner]` (the superset); one config line narrows it to `[build]`. Do not choose for me.
   - **D-17 lane keywords and platform aliases:** to be supplied.
   - **Open or closed:** the provider's status, overridden by presence on the live board **this run** (a live observation beats a feed status); never inferred from absence within a run; unknown stays unknown.
   - **Undated postings** are included and counted as `undated`; postings first seen before the window are excluded and counted as `out_of_window`.
   - **PII:** derived fields (platform sentence, title, class evidence) have email addresses and phone numbers stripped; raw text stays only in the payload store (D-06 pending counsel).
   - Caps (extractor calls, page reads, batch size, maximum description size, minimum text length for "read") are config marked `status: proposal`.
5. Do not guess provider response shapes. The pipeline consumes the connector layer's normalised record. If the real record shape is not available, define `RawPosting` as the contract, mark it `[VERIFY]`, and list what I must supply (one recorded sample per operation).
6. No vendor or broker name in `src/jobs/`, config keys, reason texts or output. Provider identity is a `provider_code` in the internal ledger only.
7. Before using any PostgreSQL feature, run it on the project's test database and read the real behaviour.

## 3. Anti-hallucination protocol (run time): the PostingGuard

Code rules, each with at least one "lying" test (section 9).

| # | Rule |
| --- | --- |
| J1 | **Counts are computed in code from posting rows** (`counts.py`, pure). No function accepts a count as input; no model or caller supplies one. Every count names its base and the invariants are asserted (J2 to J9); a violated invariant raises and the row ends Partial. |
| J2 | **"Read" means the complete description was obtained and processed this run** (`text_complete` true, length at least the configured minimum, not an excerpt). A snippet or excerpt is **not read**. Unknown completeness counts as not read. |
| J3 | **Platform sentence is extracted by deterministic code** (sentence segmentation plus lexicon match with word boundaries) as a verbatim span with offsets and a hash. A platform is "named" only if a lexicon term appears in the **description text**. Every posting that names a platform has at least one stored sentence per named platform; a mismatch is a defect. No model writes or paraphrases a sentence. |
| J4 | **Class comes from reading, never from the title alone.** Rules run first; conflicting rule hits mean ambiguous. Only the ambiguous remainder goes to the tool-less extractor, whose output is validated (class in the enum or `unclear`, one evidence sentence that must appear **verbatim in this posting's text**, the posting id must match the one sent, no extra fields, canary absent). **A Build or Admin and owner result from the extractor needs agreement from a second pass on a different model tier**; disagreement ends `unclassified(disagreement)`. |
| J5 | **Full text.** Every posting whose class is in `full_text_required_classes` must have `full_text_read` true. If only an excerpt exists, escalate with one bounded page read of the posting URL; if that fails or the cap is hit, flag `full_text_missing` and the row ends Partial. |
| J6 | **12-month window** is computed in code from the injected `as_of` date (calendar months, boundary tested to the day); no wall clock. |
| J7 | **Open or closed** follows the proposal in section 2.4; every posting records `status_source`. |
| J8 | **Dedupe** by a stable `posting_key` (provider job id if present, else canonical URL, else a content fingerprint flagged `weak_key`). The same posting from the live board and the feed merges into one row keeping all URLs. Never merge on title alone. Never double count. |
| J9 | **Zero versus not searched.** A lane or platform that was not searched (lane off, tool blocked, not configured) shows `not searched` with a reason, never 0. Veeva outside life sciences is recorded once as None (`lane_not_in_scope`). |
| J10 | **Every posting row carries its URL** (canonical plus others) and provenance (`raw_ref`, `provider_code` internal, `retrieved_at`). A record without a URL is rejected and counted. |
| J11 | **Claims are templated by code** from structured fields (one sourced claim per classified posting: platform, class, status, first seen); evidence is the posting URL and the **platform sentence**; origin and tier come from the fetch ledger. The pipeline never sets a grade and never calls a keyword match a finding; matched but unread postings are Lead candidates only. |
| J12 | **Untrusted text.** Description text goes only to the tool-less extractor; it is never interpolated into queries, URLs, file names, tool-calling prompts or logs. Strip HTML and control characters, cap lengths, check the canary. A hostile posting is excluded from classification, counted `hostile` and flagged. |
| J13 | **No constructed URLs.** The careers URL comes only from the verified setup output; posting URLs only as returned. A test scans the code for any URL built from the domain. |
| J14 | **Pull once.** Stages read the payload store; a re-run of the stage makes zero paid calls and writes identical rows. |
| J15 | Provider names, credit figures and call refs never reach any export, key, name or client-safe view. |

## 4. Architecture to build

```
src/jobs/
  config.py       loads and validates lanes, lexicon, rubric, caps; content hash recorded with graph_version
  errors.py       JobsError > InvariantViolation, WindowError, SchemaViolation, ConfigError, NotConfigured
  models.py       RawPosting (connector contract), Posting, PlatformSentence, Classification, Observation, PostingCounts (frozen pydantic)
  window.py       pure: window_start(as_of), classify_window(posting)
  normalise.py    pure: canonical URL, posting_key, text cleaning, PII strip, status normalise
  dedupe.py       pure: merge by posting_key, keep all URLs and observations
  match.py        pure: lexicon match (word boundary), title-only versus description match
  sentences.py    pure: segmentation, platform sentence spans with offsets and hash
  classify.py     pure: rule engine, ambiguity detection
  extractor.py    ExtractorPort wrapper: output schema, verbatim check, canary, second-pass agreement
  escalate.py     bounded full-text escalation (page read)
  counts.py       pure: PostingCounts, invariants, render_count_sentence()
  claims.py       templated ProposedClaims for the grading engine
  pipeline.py     orchestrates the steps over ports; batch loop; idempotent
  repo.py         PostingRepo through tenant_txn; observations
  ports.py        Protocols
  export.py       read model for the Job Postings file (class, platform sentence, full description, link, dates)
src/workflow/stages/s05_jobs.py   thin adapter (if the workflow exists)
config/{job_lanes.yaml, posting_rubric.yaml, jobs_v1.yaml}
docs/jobs/{rubric.md, counts.md}  generated; a test fails if stale
migrations/                       one expand-only revision
```
Purity rule: `window`, `normalise`, `dedupe`, `match`, `sentences`, `classify`, `counts` do no I/O and read no clock or environment.

## 5. The pipeline, step by step

1. **Live board search, one per lane keyword** (tool category from the connector, plain page reads on job pages only): record the links list and the counts the board reports **separately from** the counts computed from rows. A lane that is off is recorded once as None, not searched. Evidence for `job_board_by_lane`: one call per lane keyword (`expected_calls` = number of keywords on).
2. **12-month pull of open and closed postings** with descriptions, pulled once, paginated within the connector caps, stored raw with neutral keys. Evidence for `job_postings_12_months`. A cap, loop or failure ends with the partial set and an exit reason, never an infinite loop and never a silent stop.
3. **Normalise, dedupe, window** (J6, J8, J10): the count chain is stored: `pulled`, `deduped`, `in_window`, `out_of_window`, `undated`.
4. **Match** against the lexicon; split `matched` into description matches and `title_only` (Lead candidates).
5. **Read and extract platform sentences** (J2, J3); partial text goes to escalation (J5).
6. **Classify** (J4): rules, then extractor with second-pass agreement for Build and Admin and owner.
7. **Full-text check** (J5).
8. **Counts** (J1): per platform and lane, plus a distinct total (a posting naming two platforms counts once in each platform and once in the distinct total, and the per-platform figures are never summed).
9. **Persist and emit:** posting rows, platform sentences, classifications (append-only, a reclassification adds a row), observations (insert-only, source of `first_seen` and `last_seen`), the run-level record (window, `as_of`, lanes, caps, exit reasons, counts, config hash), templated claims to the grading engine, row evidence to the tracker.

### Counts (the contract)
Stored as integers, rendered from them, never typed: `pulled`, `in_window`, `matched`, `read`, `open`, `closed`, `unknown_status` (for the matched set and again for the read set), `by_class` (`build`, `admin_owner`, `working_tool`, `user`, `unclassified`), `with_platform_sentence`, `full_text_read`, `full_text_missing`, `title_only`, `hostile`, `undated`, `out_of_window`, each per platform. Invariants asserted in code: `open + closed + unknown = base`; `sum(by_class) = read`; `with_platform_sentence = read postings that name a platform`; `read <= matched <= in_window <= deduped <= pulled`. The rendered sentence follows the PRD example: "14 postings read of 31 matched (9 open and 5 closed of the 14 read)"; the template is config.

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
1. `job_postings`: stable `posting_key` unique per `(account_id, posting_key)`, canonical URL and other URLs, title, location, `posted_at`, status and `status_source`, `first_seen`, `last_seen`, `text_ref` and `text_sha256` (the text itself lives in the payload store), `text_complete`, `full_text_read`, current class, lane, platforms, flags (`title_only`, `weak_key`, `undated`, `hostile`). CHECKs for the status and class vocabularies and `url IS NOT NULL`.
2. Append-only tables (INSERT and SELECT grants only): `job_posting_platform_sentences` (sentence, offsets, hash, platform), `job_posting_classifications` (class, `classified_by` rule or extractor, rule id, evidence sentence, second-pass result, run), `job_posting_observations` (posting, run, `seen_at`, source category, status seen, `raw_ref`), `job_pipeline_runs` (run-level record above).
3. Forced RLS (`USING` and `WITH CHECK`, `TO app_rw`) on all of them, `tenant_id NOT NULL`, no DELETE grant. Extend `tests/isolation`; `scripts/check_rls_catalog.sql` must return no rows.

## 7. Loops (all bounded, recorded, testable)
- **Board search:** one call per lane keyword, no re-search of the same keyword.
- **Pull pagination:** the connector's page, item, byte and time caps plus cursor-repeat detection; resume from the stored cursor; exit reason recorded.
- **Batch loop:** postings are processed in batches of a configured size; one posting's failure is isolated (`unclassified(reason)` or `hostile`) and never aborts the batch.
- **Escalation loop:** at most one page read per posting and a run-level cap; stops on budget.
- **Extractor loop:** one call per ambiguous posting, at most one re-ask on schema failure, a second pass only for Build and Admin and owner, a run-level cap; ambiguous postings beyond the cap end `unclassified(cap)` and the row Partial.
- **Gap passes:** when the tracker or planner re-enters the stage (at most 2 passes), only unread, unclassified or unescalated postings are processed; no re-pull. A pass that adds no read or classified posting ends the loop with `no_progress`.
- Every loop records its exit reason (`converged`, `cap_reached`, `no_progress`, `budget`, `deadline`) in the run record. The pipeline adds **no** provider retries.

## 8. Integration
1. **Tracker:** evidence for the three rows only through `record_outcome`; map shortfalls honestly (failed pulls are failed calls; unread or unclassified remainder because of a cap sets `cap_hit`; an off lane is `out_of_scope`). The `job_postings_read_classified` row is Done only when every in-window matched posting is read and classified and every full-text-required posting is full-text read; no match across successful pulls is None.
2. **Grading engine:** claims go through `grade_claims`; hiring half-life applies; a posting read on the company's own page this run can reach Confirmed, a posting from a feed is Probable at best; the pipeline never sets either.
3. **Tech stage:** expose `platform_posting_evidence(run, platform)` for the cross-check; the cross-check logic itself belongs to the tech stage.
4. **Builders and alerts:** `export.py` feeds the Job Postings file; class and lane are stored so the `new_build_role` alert can fire (debounce is the refresh work package, not this one).
5. Metrics by outcome and class only, no tenant label. Description text, platform sentences and evidence never logged at info level (a canary test).

## 9. Test harness (build before the pipeline)
Location `tests/jobs/`, real Postgres, injected `Clock`, fakes from `tests/fakes/`. Fixtures under `tests/fixtures/synthetic/jobs/` (each named and labelled `synthetic`).
1. **Synthetic corpus:** one posting per class; platform named only in the description, only in the title, in neither; excerpt-only text; the same posting on the live board and in the feed; a repost with a new URL; open, closed, unknown; undated; one day inside and one day outside the window; two platforms in one posting; the Veeva lane off; an injection and a canary in a description; non-English text; HTML noise; a very large description; a recruiter email and phone; pagination with a duplicate page and a repeating cursor; a provider outage mid-pull.
2. **Independent oracle:** expected counts, classes and sentences for the corpus are written by hand in the test data, not computed by the code under test.
3. **Pure-logic tests:** window boundary to the day; `posting_key` stable and order independent; dedupe idempotent; lexicon matching with word boundaries (no match inside another word); sentence spans verbatim with correct offsets; every rubric rule has its positive and negative case; conflicting cues are ambiguous.
4. **Lying-model tests (every J-rule), scripted FakeLlm:** returns a class outside the enum; an evidence sentence not in the text; a sentence from another posting; a posting id that was not sent; a platform the text does not contain; extra fields; the canary; Build for a posting that reads as a User role (the second pass must disagree); a number in free text; invalid output twice; an instruction embedded in the description. Each ends `unclassified(reason)` or `hostile` with a counted reason, never a trusted class.
5. **Property tests (hypothesis):** counts invariants (section 5) hold for any generated posting set; per-platform figures are at least the distinct total; the window is monotonic in `as_of`; the pipeline terminates within every cap for any generated page and outcome sequence; counts do not change under permutation of input; no function in `counts.py` accepts a count.
6. **Stage tests:** preconditions, postconditions, owned rows, proof recorded, **idempotency** (run twice, identical rows, zero extra paid calls); gap re-entry processes only the remainder.
7. **Resume matrix:** kill points before the pull, mid-pagination, between a provider response and ledger finalisation, mid-batch, after persist but before the checkpoint; after resume the same final rows hash, no duplicate paid call beyond the documented bound, ledger sum equal to `spent_usd`.
8. **Whole-pipeline golden run** on the corpus to the oracle counts; the outage run ends with Blocked or Partial rows and reasons and partial counts that still satisfy the invariants.
9. **Database tests (raw SQL as `app_rw`):** a posting without a URL, a class outside the vocabulary or a status outside the vocabulary is refused; append-only tables refuse UPDATE and DELETE; two tenants with the same account domain never cross.
10. **Output safety:** `export.py` output and rendered count sentences pass `scripts/scan_client_output.py`; no provider code, credit figure or call ref; recruiter contact data is absent from derived fields; every number in a rendered sentence appears in `PostingCounts`.
11. **Mutation check:** run `mutmut` (or the project's tool) on `window.py`, `counts.py`, `dedupe.py`, `classify.py` if it installs; list surviving mutants or say it could not run.
Coverage: at least 90% on `src/jobs/`, **100% on `window`, `counts`, `dedupe`, `match`, `sentences`, `classify` and the extractor verification code**.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/jobs` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `models`, `window`, `normalise`, `dedupe`, `match`, `sentences`, `classify`, `counts`, `ports` and fakes, corpus and oracle, `extractor`, `escalate`, `claims`, migration, `repo`, `pipeline`, `export`, integration, docs generation.

## 11. Production requirements
Type hints everywhere and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; bounded memory (batching, size caps); config validated at startup and hashed; structured logs with run id as the trace id; one span per step and per batch; idempotent migration that runs twice cleanly; every public function documented with its guarantees; every error typed.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/jobs`; isolation suite; `pytest tests/jobs` with coverage; the full `pytest -m "not eval"`. Confirm: every config entry has a `source` or `status`; counts equal the oracle on the corpus; every J-rule has a passing lying test; no URL is constructed from a domain; no vendor name in `src/jobs/`; the docs are current.

## 13. Deliverables
`src/jobs/`, the stage adapter, the migration, `config/` files, `docs/jobs/` (rubric, counts contract), ADRs (class definitions and full-text scope, status rules, undated and out-of-window handling, PII approach), the updated open-decision register (D-16, D-17), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: a change would drop or alter an existing column; the real posting record shape is needed and no sample or spec exists (build against the contract and list what to supply); D-16 or D-17 blocks a High row without a usable default; the skill and another source conflict outside the register; you want to alter the architecture in section 0.2; real credentials, spend or tenant data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of the steps (Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: counts computed from rows with invariants, no class without a verified reading, every named platform has a verbatim sentence, idempotent and resumable, zero extra paid calls on re-run); the decisions I must make (D-16 class definitions and full-text scope, D-17 keywords and aliases, the status and undated proposals, PII approach); what I must supply (lane keywords and aliases, rubric cues, one recorded sample per posting operation); integration follow-ups; open `[VERIFY]` items; commits; the next work package.
