# Prompt: build the People pipeline (title search, two seniority passes, gap-only association search, merge, exclusions, level, lane, priority, full profiles, counts computed in code), production grade

Paste everything below the line into Claude Code, opened in the project repo (built with the `account-intelligence-platform` skill).

---

Build the **People pipeline**, the engine behind PRD step 7 (People): a **title search on the account domain** (platform ownership titles plus leadership titles), **two seniority passes** paged on the same source, **association search only to fill gaps**, then people named in research with their profile URLs; **merge by profile URL first, name second**; apply **exclusions (security, IT compliance, IT audit)**; assign **level, lane and priority**; pull **full profiles** for every senior person (and posts for priority A only); and compute **alumni, tenure and certification counts in code with their base**. This is backend work. Write production-grade code and prove it with a real harness. Never invent facts about the process, the providers, the account or any person, and never let a model decide who is in scope, who is excluded, or what a count is.

## 0. Ground rules

1. Read `.claude/skills/account-intelligence-platform/SKILL.md` first and obey it. Then read `references/01-product-and-process.md` (step 7, scope, Findings from LinkedIn, rules), `references/05-data-model.md` (`people`: merged and excluded rows are kept, never deleted), `references/06-evidence-and-grading.md`, `references/03-tenancy-isolation-security.md`, `references/04-run-lifecycle-and-agents.md`, `references/12-open-decisions-and-verification.md` (D-03 people source, D-06 people-data scope), `references/13-starter-code-and-known-defects.md`, `assets/checklist.yaml`, `assets/routing.yaml` (people routes and adapter rules), `assets/alert_rules.yaml`, `assets/client_output_denylist.txt`, `BUILD_LOG.md`, `docs/STATUS.md`. If `docs/sources/` holds the PRD, read section 3.3 (people scope), 3.4 (People) and 4.2 step 7 and cite them. Follow the skill's work loop: plan in five lines, tests first, smallest increment, run the gates, log.
2. **Fixed by the skill:** the database enforces isolation (`tenant_id NOT NULL`, forced RLS with `WITH CHECK`, roles `TO app_rw`, access through `tenant_txn`); pull once and work from stored payloads; one retry owner per failure class (the node `RetryPolicy`; this pipeline adds none); untrusted text reaches only the tool-less extractor; no fact without a URL and evidence; client-facing output never names brokers or shows credit counts; **no outreach code path and no contact-detail enrichment**; people rows are never deleted.
3. This pipeline owns the coverage rows `people_by_platform`, `full_profiles_senior_people` and `posts_priority_people` (confirm against `config/workflow_v1.yaml` if it exists). It **never writes a row status**; it hands evidence to the tracker (`record_outcome`) or, if the tracker does not exist yet, to the `CoverageRepo` port. Pass 1 key-contact profiles (`key_contact_profiles_posts`) reuse the same profile store, so a person already pulled is never pulled again.
4. No real provider calls, no real LLM calls, no real spend, no real people data. Fakes and `synthetic` fixtures only.
5. Never claim a gate passed that you did not run. Never weaken or delete a test to pass. Commit on branch `feat/people-pipeline` in small Conventional Commits. Do not push.

## 1. Preflight (before writing code)

Print a table of what exists: the connector layer (people-by-title, people-by-technology, profile and post operations, normalised person records, pagination caps, `source` handles for paging), the router and the people-source mode switch from P0-3, the payload store, `people` and related tables with exact columns, source registry and stable source codes, the tracker, the grading engine, the job pipeline's lane lexicon (`config/job_lanes.yaml`), the workflow stage `s07_people`, the extractor port, `tenant_txn`, the isolation suite.
- Missing dependency: define a typed `Protocol` in `src/people/ports.py` and an in-memory fake in `tests/fakes/`. Ports: `PeopleSearchPort` (title search with a cursor, association search), `ProfilePort`, `PostsPort`, `CostPort` (recorded cost of a call, `None` when unknown), `PayloadStore`, `PeopleRepo`, `SourceRegistryPort`, `CoverageEvidencePort`, `GradingPort`, `ExtractorPort`, `RunInputPort` (scope, lanes, reference employers, `as_of`), `Clock`. Production adapters that do not exist raise `NotConfigured`; the rows then end Not done with reason `not_available_yet`, never a guess.
- **One implementation per rule.** Reuse the grading engine's span and URL checks and the job pipeline's lane lexicon; do not copy them. If the workflow already contains people logic, move it here and leave a thin re-export.
- If a `people` table exists, **extend it expand-only** (add columns, constraints, indexes; never drop or alter an existing column; ask first if unavoidable).

## 2. Anti-hallucination protocol (build time)

1. The step wording, the default scope (Director and above, Head, Senior Manager; global; security, IT compliance and IT audit excluded), the lane names, the merge order, the profile fields, "posts for priority A only" and the counts rule come only from the sources. **The sources do not define**: the title-to-level mapping, which titles are "platform ownership" or "leadership" titles, what separates the two seniority passes, how a lane or a priority A, B or C is assigned, the exclusion cues, "top ten", "gap", "recent activity", the tenure buckets, what "alumni" means and of whom, or how certifications map to lanes. All of that is configuration with a `source` field: `source: TODO, needs product owner` where the sources are silent, `status: proposal` where I propose a default. Do not write title lists, cue lists, aliases or definitions from memory into code.
2. **Seed config minimally.** `config/people_scope.yaml` holds the scope defaults named in the PRD; `config/people_rubric.yaml` holds level tokens, lane cues (reuse the job-lane lexicon), exclusion cues, priority rules, tenure buckets, certification matching and the reference-employer aliases. Seed only unambiguous cues derived from the PRD wording (the three exclusion names; the three level names), each with a `rule_id`, `status: proposal`, and a positive and a negative test example. The product owner extends them.
3. **Proposals of mine, not facts from the sources** (config marked `status: proposal`, ADR, register entries at the next free numbers; earlier prompts used D-12 to D-20, so start at **D-21** unless the register shows otherwise):
   - **D-21 scope definitions:** level bands, which band each seniority pass searches (proposal: pass 1 Director and above plus Head, pass 2 Senior Manager), the priority A, B, C rubric, lane assignment, the "top ten" order (priority, then level, then stable key), the gap rule (proposal: a lane that is on and has no in-scope person after both passes), and `profile_levels` (default: every in-scope person gets a full profile).
   - **D-22 counts definitions:** **alumni** = an in-scope person at the account whose prior employers include an employer on the run's reference list (the pursuing client and any employers the operator names; from run input only); with no list the count is **not computed** ("waiting on the account owner"), never 0. Tenure buckets. Certification matching against the lane lexicon.
   - **D-23 data protection (D-06 pending counsel):** store only an allow-listed set of business fields (name, current title, employer, prior employers, join date, role start, certifications, location as returned, the person's own description of their work); no emails, phone numbers, personal social links, photos, age, gender or family data. A tombstone **erasure** mechanism keeps the row's identity and merge chain but nulls personal fields (`erased_at`, reason), reconciling "never delete" with a deletion process; do not expose it through the API until counsel decides.
   - Caps and thresholds (people per run, profile batch size, cost-sample size, mention-resolution queries, recent-activity window, unknown-cost post cap) are config marked `status: proposal`.
4. Do not guess provider response shapes. The pipeline consumes the connector layer's normalised `RawPerson` record. If the real record shape is not available, define the contract, mark it `[VERIFY]`, and list what I must supply (one recorded sample per operation).
5. No vendor or broker name in `src/people/`, config keys, reason texts or output. Provider identity is a `provider_code` in the internal ledger only; the router picks the provider (single or dual people-source mode), the pipeline asks for a need.
6. Before using any PostgreSQL feature (cycle-prevention trigger, partial unique index), run it on the project's test database and read the real behaviour.

## 3. Anti-hallucination protocol (run time): the PersonGuard

Code rules, each with at least one "lying" test (section 9).

| # | Rule |
| --- | --- |
| P1 | **Identity data comes only from connector records with provenance.** Names, titles, employers, dates and certifications are never generated, completed or corrected by a model. |
| P2 | **A person in the list carries a canonical profile URL.** A person named in research without one stays `unresolved` with the mention URL and sentence, gets a bounded resolution attempt, and is not in the People List until a profile URL is found. |
| P3 | **Merge safety.** Merge on the canonical profile URL first (tracking parameters, trailing slashes, locale hosts and case normalised). Merge on name only when the normalised names are equal **and** at least one corroborating signal agrees (same employer domain, same lane, similar title); otherwise flag `possible_duplicate` and do not merge. Merges are union-find over the whole batch (order independent), reversible, recorded as events, and write `merged_into` pointing to a canonical row that is itself not merged; no cycles; **no row is ever deleted**. |
| P4 | **Scope is decided by deterministic rules** with a `rule_id` and the matching title substring as evidence. Exclusion beats inclusion. A title that matches an exclusion cue is `excluded(reason)`; an ambiguous title is `review(reason)`; an unknown level is `review(level_unknown)`; a person who is not current at the account is `out_of_scope(not_current)`. **No model decides inclusion or exclusion.** An optional extractor proposal for ambiguous titles can only route to `review` with evidence. |
| P5 | **Nothing disappears silently.** Every found person ends in exactly one status: `in_scope`, `out_of_scope(reason)`, `excluded(reason)`, `review(reason)`, `unresolved` or `merged`. The status counts are computed from rows and the identity `found = distinct + merged_away` is asserted. |
| P6 | **Counts are computed in code** (`counts.py`, pure) and name their base: alumni of `profiles read`, tenure of `profiles with a join or start date`, certifications of `profiles that provide the field`. Unknown is counted as unknown, never as zero; "field not provided" is not "none listed". No function accepts a count as input. |
| P7 | **Tenure** is computed from join date and role start with the injected `as_of` date, with the date precision kept (year only, month only). A future date, a role start before the join date, or an impossible value is flagged `date_invalid` and excluded from tenure counts. |
| P8 | **Certifications** are read from the structured field and matched to a lane by the lexicon; never inferred from a title or a description. |
| P9 | **Data minimisation.** The profile model is allow-list based (`extra='forbid'`); anything else a provider returns is dropped at the boundary and counted; personal contact data never reaches a database column, a claim, a log or an export. Raw payloads stay only in the payload store under neutral keys. |
| P10 | **Pull once, never re-add.** Pages continue the same source from its cursor; a person already pulled (by canonical URL, in this run or already stored for the account) is never pulled again; a cursor repeat or a page with no new person ends that source with `no_new_people`. |
| P11 | **Posts only for priority A**, only recent, and only after a **cost-sample gate**: pull a configured sample, take the recorded cost from the ledger, project the total, continue only within the remaining budget share. Unknown cost (`None`) is never treated as zero: pull at most the configured unknown-cost cap and flag it. A provider without a last-activity date is pulled and flagged `activity_unfiltered`. |
| P12 | **Distribution rules.** Payloads from a provider flagged `internal_only` in the adapter rules (the organisation provider's output must never reach a hosted file) carry that flag; they never enter an export, the workspace zip or a client-safe view. Records from two providers about one person are not independent evidence unless the source registry says so (grading rule G11). |
| P13 | **Untrusted text.** Profile descriptions and posts go only to the tool-less extractor, never into queries, URLs, file names, tool-calling prompts or logs; strip control characters and markup, cap lengths, check the canary. A hostile profile is excluded from Findings, counted `hostile`, flagged. The extractor's only outputs are Findings candidates: a claim plus **one verbatim sentence** from that person's own text, schema-validated, then checked by the grading engine's span check. |
| P14 | **No outreach and no contact enrichment.** The pipeline never calls an operation that returns emails or phone numbers; a test scans the operation names it uses against an allow-list. |
| P15 | **Findings and claims go through the grading engine**; the pipeline never sets a grade. A person's title claim is templated by code from structured fields, evidence is the profile URL (registered as a source with a stable code) and the verbatim field string. |
| P16 | **Exports** (`export.py`) contain only `in_scope` rows; excluded, review, unresolved, merged and erased people never appear by name in any export; the Summary sheet shows **counts by status and reason, never names**; no provider code, credit figure or call ref appears. Another account's people never appear. |

## 4. Architecture to build

```
src/people/
  config.py     loads and validates scope, rubric, caps; content hash recorded with graph_version
  errors.py     PeopleError > InvariantViolation, MergeError, SchemaViolation, ConfigError, NotConfigured
  models.py     RawPerson (connector contract), Person, Profile, Post, Classification, PeopleCounts (frozen pydantic, allow-list)
  urls.py       pure: canonical profile URL
  names.py      pure: name normalisation and comparison
  merge.py      pure: union-find merge plan with corroboration rules (P3)
  level.py      pure: title to level band
  lane.py       pure: title and headline to lane (shared lexicon)
  exclusion.py  pure: cues to include, excluded(reason), review(reason)
  priority.py   pure: rubric to A, B or C with rule ids
  scope.py      pure: combines the above into one status per person
  tenure.py     pure: date math with precision and validity
  certs.py      pure: certification to lane matching
  alumni.py     pure: employer normalisation and reference-list matching
  counts.py     pure: PeopleCounts, invariants, render_count_sentence()
  search.py     title search passes with paging over ports (bounded)
  gaps.py       gap detection and the association search (bounded)
  mentions.py   resolve people named in research to profile URLs (bounded)
  profiles.py   full-profile pull loop, pull once
  posts.py      cost-sample gate and priority A posts
  findings.py   extractor wrapper producing ProposedClaims
  claims.py     templated person claims
  pipeline.py   orchestrates the steps over ports; batch loop; idempotent
  repo.py       PeopleRepo through tenant_txn; events
  ports.py      Protocols
  export.py     read model for the People List and its Summary sheet
  erasure.py    tombstone mechanism (not exposed through the API)
src/workflow/stages/s07_people.py   thin adapter (if the workflow exists)
config/{people_scope.yaml, people_rubric.yaml}
docs/people/{rubric.md, counts.md}  generated; a test fails if stale
migrations/                         one expand-only revision
```
Purity rule: `urls`, `names`, `merge`, `level`, `lane`, `exclusion`, `priority`, `scope`, `tenure`, `certs`, `alumni`, `counts` do no I/O and read no clock or environment.

## 5. The pipeline, step by step
1. **Title search on the domain.** For each lane that is on: the platform ownership titles and the leadership titles from config; **pass 1** and **pass 2** by seniority band (D-21), each paged on the **same source** from its cursor. Evidence for `people_by_platform`: one call sequence per (lane, pass), `expected_calls` = number of planned (lane, pass) searches. A lane that is off is recorded once as None, not searched.
2. **Normalise and merge** (P3): canonical URLs, union-find merge plan, events written; counts `found`, `distinct`, `merged_away`, `possible_duplicate`.
3. **Scope** (P4, P5): current-employer check, exclusions, level, lane, priority, each with `rule_id` and evidence.
4. **Gaps** (section 7): for each lane that is on with no in-scope person, one association search restricted to IT, engineering and product functions through the router (need `people_by_technology`), then re-merge and re-scope. Anything still empty is recorded honestly (None or Partial), never padded.
5. **People named in research:** read from stored facts and sources of earlier stages; resolve each to a profile URL with a bounded number of queries (P2); the rest stay `unresolved`.
6. **Full profiles** for every person in `profile_levels` (P10): prior employers, join date, role start, tenure, certifications, location, the person's own description (P9). Evidence for `full_profiles_senior_people`.
7. **Posts and comments for priority A only** (P11). Evidence for `posts_priority_people`; store `last_activity_at` so the `priority_a_posts` alert can fire later.
8. **Counts** (P6, P7, P8): people by status and reason, profiles read, alumni, tenure buckets, certifications per lane, each with its base, rendered from integers.
9. **Findings from LinkedIn** (P13, P15): extractor candidates with a verbatim sentence, graded by the grading engine; stored separately from the people list.
10. **Persist and emit:** rows, append-only histories, the run-level record (scope, passes, caps, exit reasons, counts, config hash), templated claims, row evidence, and `top_people(run, n)` in the deterministic D-21 order for the name-searches stage.

### Counts (the contract)
Stored as integers, never typed: `found`, `distinct`, `merged_away`, `possible_duplicate`, `in_scope`, `out_of_scope` (per reason), `excluded` (per reason), `review` (per reason), `unresolved`, `profiles_read`, `profiles_partial`, `alumni` (or `not_computed` with its reason), `tenure_known`, `tenure_unknown`, `date_invalid`, tenure per bucket, `cert_field_provided`, `cert_field_absent`, certified per lane, `posts_pulled`, `activity_unfiltered`, `hostile`, `fields_dropped`. Invariants asserted: `found = distinct + merged_away`; `distinct = in_scope + out_of_scope + excluded + review + unresolved`; `tenure_known + tenure_unknown + date_invalid = profiles_read`; per-lane figures are never summed into a distinct total. The rendered sentence names its base ("5 of 14 profiles read list a prior employer on the reference list").

## 6. Database (one expand-only revision; follow `scripts/lint_migrations.py` rules R1 to R5 including `lint:covers`)
1. `people`: stable `person_key`, `profile_url` and `profile_url_canonical` (unique per `(account_id, profile_url_canonical)` where not null), name, title, level, lane, priority, `scope_status`, `scope_reason`, `rule_id`, `merged_into` (CHECK `merged_into <> id`; a trigger or deferred constraint prevents pointing at a merged row, verified on the test database), `exclusion_reason`, `employment_status`, `erased_at`, flags. CHECKs: `scope_status = 'in_scope' => profile_url IS NOT NULL`; `scope_status = 'excluded' => exclusion_reason IS NOT NULL`; vocabularies for level, priority (A, B, C) and status. **No DELETE grant.**
2. Append-only tables (INSERT and SELECT grants only): `person_sources` (provider code internal, `raw_ref`, pass, lane, query), `person_profiles` (allow-listed fields, own description with span hash, `date_precision`), `person_posts` (post URL, `posted_at`, a length-capped verbatim excerpt, `raw_ref`), `person_events` (merge, unmerge, scope change, actor, `rule_id`), `people_pipeline_runs` (run-level record).
3. Forced RLS (`USING` and `WITH CHECK`, `TO app_rw`) on all of them, `tenant_id NOT NULL`. Extend `tests/isolation`; `scripts/check_rls_catalog.sql` must return no rows.

## 7. Loops (all bounded, recorded, testable)
- **Search loop:** per (lane, pass) source, paginate within the connector caps and a per-run people cap; **no-progress detection** (a page with no new canonical person ends the source with `no_new_people`); cursor-repeat handling from the connector; resume from the stored cursor.
- **Gap loop:** at most **one** association search per lane that is on and has a gap, never repeated; the planner or tracker may re-enter the stage at most 2 times, and a pass that adds no new in-scope person ends the loop (`no_progress`).
- **Mention-resolution loop:** at most the configured number of queries per mentioned person and a run cap.
- **Profile loop:** one pull per person, in batches; one person's failure is isolated (`profile_failed(reason)`) and never aborts the batch; stops on budget.
- **Posts loop:** sample first, then only the budget-approved remainder (P11).
- **Merge fixpoint:** the union-find plan is computed once; a check re-runs it and must produce the same plan (cap 2); a differing plan raises `MergeError`.
- Every loop records its exit reason (`converged`, `cap_reached`, `no_progress`, `budget`, `deadline`) in the run record. The pipeline adds **no** provider retries.

## 8. Integration
1. **Tracker:** evidence for the three rows only through `record_outcome`; map shortfalls honestly (failed searches are failed calls; people or profiles left because of a cap set `cap_hit`; an off lane is `out_of_scope`). `full_profiles_senior_people` is Done only when every person in `profile_levels` has a read profile.
2. **Grading engine:** claims through `grade_claims`; a profile page is not automatically primary; the pipeline never sets a grade.
3. **Workflow:** `s07_people` is a thin adapter; the name-searches stage consumes `top_people`.
4. **Builders and alerts:** `export.py` feeds the People List (title, level, lane, priority, profile URL, source code) and its Summary sheet; class and priority are stored for the alert rules.
5. Metrics by outcome only, no tenant label. Names, titles, descriptions and posts never logged at info level (a canary test).

## 9. Test harness (build before the pipeline)
Location `tests/people/`, real Postgres, injected `Clock`, fakes from `tests/fakes/` including both people-source modes (single and dual). Fixtures under `tests/fixtures/synthetic/people/`, every file named and labelled `synthetic`.
1. **Synthetic corpus with a hand-written oracle:** profile URL variants of one person (case, trailing slash, tracking parameters, locale host); two different people with the same name; a name-only duplicate with and without corroboration; security, IT audit and IT compliance titles; an ambiguous compliance title; a Senior Manager, Head, Director, VP, Chief and a plain Manager (out of scope); a person not current at the account; unknown level; missing join date, a future join date, month-only dates; certifications present, empty and not provided; prior employers with alias variants and no reference list; profile text with an injection and the canary; a profile returning email and phone fields; priority A with and without a last-activity date; pagination with a duplicate page and a repeating cursor; a provider outage mid-search; a lane with no results; non-global scope.
2. **Pure-logic tests:** canonical URL; name comparison; union-find plan is order independent, idempotent and acyclic; every rubric rule has a positive and a negative case; exclusion precedence; tenure arithmetic across precision and `as_of`; certification and alumni matching.
3. **Lying tests (every P-rule), scripted FakeLlm and fake providers:** the extractor invents a person, an employer or a title; returns a sentence not in the person's text; adds an email; embeds an instruction; echoes the canary; tries to include or exclude someone (no such field: rejected); a provider returns a person at another company, a former employee, a contact field, or a cost of `None`. Each ends dropped, `review`, `hostile` or capped with a counted reason, never a trusted value.
4. **Property tests (hypothesis):** counts invariants hold for any generated people set; merge is order independent; `merged_into` never cycles and always points to a canonical row; tenure monotonic in `as_of`; the pipeline terminates within every cap for any generated page and outcome sequence; no export contains an excluded, review, unresolved, merged or erased name; no function in `counts.py` accepts a count.
5. **Stage tests:** preconditions, postconditions, owned rows, proof recorded, **idempotency** (run twice, identical rows, zero extra paid calls; a person already pulled is not pulled again), gap re-entry processes only the remainder, both people-source modes reach the same oracle where the fake data allows.
6. **Resume matrix:** kill before the first search, mid-pagination, between a provider response and ledger finalisation, mid-merge persist, mid-profile batch, after persist but before the checkpoint; after resume the same final rows hash, no duplicate paid call beyond the documented bound, ledger sum equal to `spent_usd`.
7. **Whole-pipeline golden run** to the oracle; the outage run ends with Blocked or Partial rows and reasons with invariants intact; the cost-sample gate stops posts when the projection exceeds the budget share.
8. **Database tests (raw SQL as `app_rw`):** an in-scope person without a profile URL, a self-merge, a merge into a merged row, an excluded person without a reason are refused; DELETE is refused; append-only tables refuse UPDATE and DELETE; two tenants with the same account domain never cross; erasure keeps identity and the merge chain.
9. **Output safety:** `export.py` output and rendered sentences pass `scripts/scan_client_output.py`; no provider code, credit figure, call ref, contact detail or excluded name; every number in a rendered sentence appears in `PeopleCounts`; payloads flagged `internal_only` never reach an export.
10. **Mutation check:** run `mutmut` (or the project's tool) on `merge.py`, `scope.py`, `exclusion.py`, `counts.py`, `tenure.py` if it installs; list surviving mutants or say it could not run.
Coverage: at least 90% on `src/people/`, **100% on `urls`, `names`, `merge`, `level`, `lane`, `exclusion`, `priority`, `scope`, `tenure`, `certs`, `alumni`, `counts`**.

## 10. Development loop (bounded)
For each component repeat at most **5 iterations**: write or extend failing tests; smallest implementation; run its tests, `ruff`, `mypy --strict src/people` and the isolation suite; if red, change the **code**. After 5 red iterations on one component, STOP and report the failing test, its output, what you tried and your hypothesis. Do not weaken the test, mock the unit under test, skip it, or mark it `xfail` (except for a defect already recorded in `references/13` or an ADR).
Build order: `config`, `errors`, `models`, `urls`, `names`, `merge`, `level`, `lane`, `exclusion`, `priority`, `scope`, `tenure`, `certs`, `alumni`, `counts`, `ports` and fakes, corpus and oracle, `search`, `gaps`, `mentions`, `profiles`, `posts`, `findings`, `claims`, migration, `repo`, `pipeline`, `export`, `erasure`, integration, docs generation.

## 11. Production requirements
Type hints everywhere and `mypy --strict`; no bare `except`; no global mutable state; no clock or randomness in pure modules; bounded memory (batching, size caps); config validated at startup and hashed; structured logs with run id as the trace id; one span per step and per batch; idempotent migration that runs twice cleanly; every public function documented with its guarantees; every error typed.

## 12. Gates before you finish
Run and report real results: skill self-test; `validate_assets.py`; `lint_migrations.py`; `check_rls_catalog.sql` (no rows); `ruff check`; `mypy --strict src/people`; isolation suite; `pytest tests/people` with coverage; the full `pytest -m "not eval"`. Confirm: every config entry has a `source` or `status`; counts equal the oracle; every P-rule has a passing lying test; no contact operation is used; no vendor name in `src/people/`; the docs are current.

## 13. Deliverables
`src/people/`, the stage adapter, the migration, `config/` files, `docs/people/` (rubric, counts contract), ADRs (scope and rubric, merge rules, counts definitions, data minimisation and erasure, distribution rules), the updated open-decision register (D-21, D-22, D-23), tests and fixtures, updated `BUILD_LOG.md` and `docs/STATUS.md`.

## 14. Stop rules
Stop and ask me if: a change would drop or alter an existing column; the real person record shape is needed and no sample or spec exists (build against the contract and list what to supply); D-21 or D-22 blocks a High row without a usable default; counsel input is required to store a field (leave it out and list it); the skill and another source conflict outside the register; you want to alter the architecture in section 0.2; real credentials, spend, or real people data would be needed; a gate stays red after 5 iterations.

## 15. Final reply (short)
A table of the steps (Done, Partial, Blocked, and what each depends on); gates run with results; what is **proven by tests** versus assumed (state precisely: no person without a profile URL in the list, merges reversible and cycle free, nothing deleted, every found person in exactly one status, counts computed from rows with invariants, no model decides scope, no contact data stored, idempotent and resumable); the decisions I must make (D-21 scope and rubric, D-22 alumni and tenure definitions, D-23 minimisation and erasure); what I must supply (title lists, exclusion and priority cues, reference employers, one recorded sample per people operation); integration follow-ups; open `[VERIFY]` items; commits; the next work package.
