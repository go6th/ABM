# Merge of `claude/busy-thompson-0cc98e` and the migration renumbering, 2026-10-30

Nothing here is committed, pushed or deployed. The merge is open in the working tree
(`git merge --no-commit --no-ff claude/busy-thompson-0cc98e`, `MERGE_HEAD` set), so the owner's
commit can be a real merge commit. No live provider was called, no real data touched.

## 1. Why this was needed

Staging ran code from `claude/busy-thompson-0cc98e` (five commits on the common base 7e0ad095:
the parity bundle, account delete, the docker source-map and release-gate fixes, the DELETE-body
proxy fix). Its migrations are `0087_account_delete` and `0088_account_bundles`, and they are
live. The overnight branch (`feat/final-product`) had numbered its own migrations
`0087_posting_fields` and `0088_posting_heuristic`, the same numbers. The live database therefore
holds the other branch's 0087 and 0088 and then our 0089 to 0093 (applied 2026-10-30 11:43 UTC by
upgrading from 0088). The posting migrations never ran there: `job_postings` has no `last_seen`,
`seniority`, `new_since_previous`, `job_pipeline_runs` has no `previous_run_id`, `carried`, and the
`classified_by` check lacks `'heuristic'`, which is why the live jobs pipeline failed with
`ProgrammingError: column "last_seen" of relation "job_postings" does not exist`.

## 2. What was merged and how each conflict group was resolved

31 files conflicted (git reports 32 paths with the two auto-merge duplicates below). Rule used:
the link archive exists on both branches; the other branch's copy is byte-for-byte the first
version we ported (`f8e2ebff`), ours is newer and went through the security and leakage waves
(`2026-10-29-fix-wave-b-done.md`, `2026-10-30-fix-wave-d2-done.md`), so OURS wins every overlapping
file and only genuinely new behaviour of theirs was added.

| Group | Files | Resolution |
| --- | --- | --- |
| Link archive, identical to our first port | `account_intelligence/archive.py`, `agents/production/payloads.py`, `agents/production/people_links.py`, `people/archive_links.py`, `people/mentions.py`, `tests/people/test_archive_links.py`, `tests/replica/test_link_archive.py` | theirs equals `f8e2ebff:<file>` (diff empty), so ours as is |
| Link archive writers with later hardening | `agents/production/pipelines.py`, `agents/production/provider.py`, `agents/pull/agent.py`, `jobs/repo.py`, `people/repo.py`, `tests/agents/test_production_adapters.py`, `tests/people/test_repo_db.py`, `tests/fixtures/synthetic/people/oracle.yaml` | ours. Their only non-archive content is the speed work (query fan-out, page reads side by side, store fan-out), which our files already carry in their own form (`QUERIES_IN_FLIGHT`, `READS_IN_FLIGHT`, `STORES_IN_FLIGHT`) |
| API contract and routes | `api/contract.py`, `api/routes/account_workspace.py`, `api/routes/health.py` | ours, then re-applied from theirs: `deleted_at IS NULL` in `account_id_of`. `EXPECTED_REVISION = "0095"` |
| Graph and wiring | `agents/graph.py`, `agents/production/wiring.py` | ours, plus their parity hook: `AbmDeps.parity`, the build node's bundle step (now with a 420 s ceiling and allow-listed log fields), `parity_port(deps, os.environ)` in `abm_deps` |
| Web | `SheetView.tsx`, `sheet-view.test.tsx`, `web/lib/proxy-body.ts`, `web/ui/components.css` | ours for the archive viewer and the file-shaped sheet; `proxy-body.ts` takes ours plus `accounts/<d>/parity/files/` as a transfer path; `components.css` keeps both blocks (archive page, delete panel and parity card) |
| Generated | `docs/api/openapi.json`, `web/lib/gen/api.gen.ts`, `docs/architecture/loops.md`, `docs/WIRING.md` | taken from ours then regenerated (`export_openapi.py`, `gen_web_client.py`, `gen_loops.py`, `wiring_audit.py`), never hand-merged |
| Config, tests, notes | `config/ownership.yaml` (both lines), `tests/isolation/matrix/matrix.yaml` (archive list and the six parity routes), `migrations/README.md`, `unwanted-things/README.md` (both inventories) | both sides kept |
| Auto-merged without a conflict but wrong | `replica/sheets.py` and `account_workspace.py` | git added their copy of `attach_archives` and `account_archive` beside ours (ruff F811, two definitions of one function). Reset to ours |

Kept whole from the other branch (no overlap): `src/parity/*`, `render/docx/*`, `scripts/parity/*`,
`config/parity/*`, `api/routes/parity.py`, `agents/production/parity_build.py`, the account-delete
route and lifecycle functions, the `deleted_at` filters of the account readers they wrote
(`accounts`, `crm`, `signals`, `chat/grounding`, `replica/answers`, `replica/workspace`),
`web/components/accounts/DeleteAccount.tsx`, `ParityWorkspace.tsx`, `ParityTable.tsx`,
`web/lib/parity/*`, the DELETE-body fix in `web/app/v1/[[...path]]/route.ts`, the Dockerfile
changes (renderer build, source maps removed), `pyproject.toml`/`uv.lock` (fastembed 0.9.1, the
`parity` package), `web/package.json` (next pinned 16.4.1), `docs/parity`, `docs/STRUCTURE.md`,
their tests (`tests/parity`, delete test in `tests/phase3/test_ws5_runs.py`, the web unit and e2e
specs).

Not ported, on purpose: three speed hunks of their `graph.py` that ours does not have (careers page
choice beside the extraction calls, the jobs and tech pipelines side by side, the client and
internal builds side by side). Our `graph.py` carries the orchestration fixes of wave D1 around
exactly those nodes (retryable errors re-raised, failed pipeline to PARTIAL, token guard not
cancelling siblings), and each hunk saves seconds on a run of tens of minutes. Easy to port later;
see owner decision 4.

A change by someone else was already in the working tree and got staged with the merge: the GCS
connection pool widening (`platform/gcp/adapters.py`, `app/container.py`, `tests/unit/test_gcs_pool.py`,
`widen_pool`). It is not part of this work; it is only staged.

## 3. Migrations

Final chain on a fresh database (alembic heads: one, `0095`):
`0086_link_archive`, `0087_account_delete`, `0088_account_bundles`, `0089_people_fields`,
`0090_tech_stack_items`, `0091_erase_new_tables`, `0092_archive_erasure_meta`,
`0093_leakage_fix_wave_d2`, `0094_posting_fields`, `0095_posting_heuristic`.

* `0087_account_delete` and `0088_account_bundles` (sql, down.sql, versions): untouched, byte for byte
  (`git diff claude/busy-thompson-0cc98e -- migrations/0087_account_delete* migrations/0088_account_bundles* migrations/versions/0087_account_delete.py migrations/versions/0088_account_bundles.py` is empty). `0086_link_archive` is identical on both branches.
* Ours renumbered with `git mv`: `0087_posting_fields` to `0094_posting_fields` (revision `0094`,
  down_revision `0093`), `0088_posting_heuristic` to `0095_posting_heuristic` (revision `0095`,
  down_revision `0094`); sql, down.sql, version file, headers, comments, the README landmark list and a
  numbering note, `EXPECTED_REVISION`, the docstring in `tests/jobs/test_repo_db.py`.
* Both are idempotent now: `ADD COLUMN IF NOT EXISTS`; the `ck_job_pipeline_runs_carried` check added
  inside `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_constraint ...) $$`; the heuristic constraint
  `DROP CONSTRAINT IF EXISTS` then added with the wider list. The two down scripts are guarded the
  same way and 0095 down relabels `heuristic` rows to `rule` first (unchanged).
* The migration lint has no `-- reviewed:` requirement for these (no SECURITY DEFINER, no new table).

### Reproduction of the staging state (scratch databases `mig_a`, `mig_b`)

Because the posting migrations now come after 0093, upgrading a fresh database to `0093` IS the
staging schema (no posting columns, check without `heuristic`, `ck_job_pipeline_runs_carried`
absent). Run on `mig_b`:

1. `alembic upgrade 0093`: `current` = 0093; no row for the five columns; check =
   `classified_by = ANY (rule, extractor, none)`; `ck_job_pipeline_runs_carried` count 0.
2. `alembic upgrade head`: runs 0093 to 0094 to 0095 only; columns `carried`, `last_seen`,
   `new_since_previous`, `previous_run_id`, `seniority` now exist; check now lists `heuristic`;
   `ck_job_pipeline_runs_carried = CHECK (carried >= 0)`.
3. On `mig_a` (already at head): `alembic stamp 0093`, `alembic upgrade head`: both run to the end
   without error over a schema that already holds every piece.
4. Round trips on `mig_a`: `downgrade 0093` then `upgrade head`; `downgrade 0094` then `upgrade head`:
   all clean, `current` = 0095.
5. `check_rls_catalog.sql` on the migrated database: no violations. `lint_migrations.py migrations`:
   `OK: 186 file(s)`. `gen_isolation_matrix.py --check`: every discovered object covered.

These steps are also a test now: `tests/db/test_posting_migrations.py` (staging state by the two down
scripts, apply, apply twice, apply over a partly migrated schema, down and up, chain linear with the
live names and revisions pinned).

`alembic check` still reports drift on `accounts`, `runs`, `sources`, `account_source_counters`; it did
before this merge (D2 note) and now also lists `accounts.deleted_at/deleted_by` and `ix_accounts_live`
because the ORM model of `accounts` does not declare them. Not a schema problem.

Deploy order stays: migrate job (api image) before the services; the live database only needs
`0094` and `0095`, both safe to run once and again.

## 4. Fixed so the guard tests pass again (section C of the brief)

* `config/deletion.yaml`: `account_bundles` classified `blocked` (insert-only table that references
  the blocked `accounts`; the rendered objects are removed by prefix with the tenant's stored
  objects), `accounts` reason names the hidden-deleted rows (`accounts.deleted_at`, `deleted_by`).
  `tests/system/test_erasure_classification.py` green. Moving it to `erase` needs a migration (owner
  decision 3).
* `config/ownership.yaml` (`account_bundles`), `tests/system/test_single_write_paths.py`
  (`account_bundles` owned by `parity/service.py`), `tests/isolation/matrix/matrix.yaml`.
* `.env.example` (`PARITY_BUNDLE`, `PARITY_DOCX_CLI`), `docs/ENVIRONMENT.md` (already generated).
* `parity/render.py` found the Word renderer through `Path(__file__).parents[2]`, which the static
  guard forbids and which is wrong in the image: now `PARITY_DOCX_CLI` or the config directory's parent.
* `scripts/check_telemetry.py`: `parity/render.py`, `parity/workspace.py`, `parity/gate/__main__.py` are
  command-line modules; `parity_build.py` and `parity/service.py` log through `obs.logging.logger`
  with allow-listed fields (`reason`, `error_class`, `state`).
* Regenerated: `docs/api/openapi.json`, `web/lib/gen/api.gen.ts` and `labels.gen.ts`,
  `docs/WIRING.md`, `docs/architecture/loops.md`.
* `pyproject.toml`: `parity` added to import-linter `root_packages` and coverage `source_pkgs`
  (the package-list guard); the contracts stay 19 kept. `parity/config.py` is now the one reader of
  the parity YAML (`tests/guardrails/test_config_readers.py`, entry `parity.config`). The held-back
  queries live in `parity/service.py`, not in the route (the raw-SQL-in-routes ratchet).
* Smaller: a long line in `tests/replica/test_credits.py` that was already failing `ruff`,
  `%`-formatting in their `tests/agents/test_speed_measure.py`, the reference account's name removed
  from two of their tests (`web/tests/unit/v1-proxy.test.ts`, `tests/parity/test_text.py`), the
  archive-pages web test mocked `useRouter` for the new `DeleteAccount` on the account page.

## 5. Exports and the parity bundle together

Both are built from the SAME mapping: `ParityBuildPort` takes the export builder's reader
(`deps.builder.reader`) and `exports.builder.map_snapshot` makes the models the bundle is copied
from, so the rows, labels, citations, exclusions and the off-account filtering of the files are the
bundle's. Their behaviour toward each other:

1. A bundle failure never fails or holds a run. The graph wraps the call in `except Exception`
   (recorded as `build.parity_failed`, counters `parity_skipped`) and now also in a 420 s ceiling
   (`PARITY_CEILING_S`); the render itself runs outside any database transaction and gives up at
   400 s. A repeated build node (it is retried on infrastructure errors) finds the run's built
   version under the same exclusion set and returns it: no second render, an analyst's edit is not
   overwritten. Tests: `tests/agents/test_parity_hook.py` (raises, skip, built, failed gate, hangs).
   The switch stays `PARITY_BUNDLE` off by default.
2. Guards, one by one:

| Protection the exports have | In the bundle |
| --- | --- |
| Current exclusions win | The content is read by the export reader, which applies the tenant's CURRENT exclusions. The manifest records `exclusion_hash`; every parity route holds back (404, and `versions` says `held_back: exclusions`) a version built under another set, and an edit of one is refused (409 `package_stale`). Test: new exclusion after a build, next build drops the person |
| Excluded company domain | `versions`, `bundle`, `screen` answer 404 for an account whose own domain is excluded |
| PII stripped from archived copies | The bundle holds no archived copy (it stores rows, not bodies). The archived-copy address columns come through the export mapping (secrets stripped, `urls.py`) |
| No provider/tool names, credit counts | The parity gate already scanned tool names, provider hosts, guarded names. New: the export build's own leak scan (`exports.verify.scan_package`: client-output denylist with credit counts, provider codes, internal hashes, plus the run's restricted terms) runs over each client Word/Excel file, the workspace zip and the client view of the bundle document, as one gate check `client_output_scan`. A hit fails the version, the report names a file and a count (never the pattern), and a version that failed this scan stores nothing (not even the bundle document) |
| Off-account and lookalike filtering | The run's restricted terms (`exports.readers.common.restricted_of`: other accounts' names incl. deleted ones, excluded companies, unlisted people's names) feed that scan; the same set is recomputed for an analyst edit |
| Deletion and erasure | `account_bundles` classified in `config/deletion.yaml` (blocked, see above); a deleted account: all parity routes 404 through `account_id_of`, adding the domain again serves its versions again; a version listing a person erased after it was built is held back (`held_back: erasure`) |
| Run-level quality verdict | Exports hold back a failed run's files. The bundle is held back on any failed latest verdict (a file-level defect too, because the bundle document carries all tables at once) |
| Tenant scoping, RLS | `account_bundles` has tenant plus owner RLS (`tenant_owner_isolation`), the routes read in `tenant_txn`, keys are checked with `require_owned`, the isolation matrix lists the six GET routes and the edit. Test: another tenant and a colleague of the owner read 404 |

Gaps that could not be closed (they are the exports' own gaps too):
* Stored objects (bundle document, rendered files, zip) of a version that exclusions or an erasure
  have overtaken stay in the payload store, unreachable through the API, until the tenant's prefix
  is deleted. `account_bundles` is insert-only, so the row stays.
* A person's erasure does not rewrite a stored version, it holds it back until the next build
  (counsel's D-06 decision covers both pipelines).
* The parity routes serve only to the account's owner and admin of that owner's rows (RLS), like
  every private-workspace table; a colleague admin does not see the bundle.

3. One account page, two views. `web/app/(client)/accounts/[domain]/page.tsx` draws three things from
   three sources: the tiles and the Deep dive list (`ws.counts`: live rows, newest equal row across
   runs, people plus people facts, open postings, detections: the research's holdings), the Files
   card (`ws.files`: the newest stored export file per kind), and `ParityWorkspace` (the bundle: the
   rows of the delivered files, one research). The file-shaped tabs behind the Deep dive links
   (`/people`, `/jobs`, ...) are the export files read back, so they and the parity tables are the
   same rows of the same mapping when they are of the same research. Where they differ today is
   count definition, not data: Deep dive counts everything held, the files print the listed rows
   (for example 371 listed people against 736 held). Decision, evidence-based and without deleting
   either: (a) the parity screen now carries `current` (its run is the run of the Files card's
   newest files, or nothing is delivered yet) and the card says "an earlier research" with a banner
   when it is not; (b) the card states what its counts are ("the rows of the delivered files") and
   that the Deep dive list counts everything held; (c) a version held back by exclusions, erasure or
   the quality check is not drawn at all, so a stale view can never sit beside a current one.
   Tests: `web/tests/unit/parity-workspace.test.tsx`, `test_the_screen_says_when_the_version_is_of_an_earlier_research`.
4. Time. Measured on the synthetic run the export tests use (30 people, 40 postings; real
   Postgres, real LibreOffice and Node, the production subprocess renderer for the exports; three
   runs): export build 2.3 s, parity bundle 7.4 s (Word and workbook render 1.5 s, gate 5.8 s of
   which three LibreOffice conversions 1.4 to 1.7 s each). Scaling the bundle's rows (render and
   gate only, same machine): 40 postings and 30 people 8.6 s, 700 and 150 16.1 s, 2,000 and 400
   29.3 s. So the step is about 3 times the export build but seconds next to a research of tens of
   minutes (the fake harness run takes 1.2 s only because every outside call is simulated, so
   "doubling" there means nothing; the harness test only proves the step is optional and bounded).
   The 420 s ceiling caps the worst case; with the switch off or a tool missing the step costs
   nothing (it skips at once). Scripts: scratchpad `measure_parity.py`, `scale_probe.py`.

## 6. Audit findings fixed (section D)

Deleted accounts (`accounts.deleted_at`) must vanish from every listing and lookup; the other
branch covered about eight readers. Found and fixed (each `FROM/JOIN accounts` read in `src` was
checked, 58 sites): Ask (`api/routes/ask.py`, `chat/store.py`), the account's export list and a
package by id (`routes/exports.py`), runs (`routes/runs.py`: by-name lookup, list, detail,
resumable), resume (`routes/research.py`), the schedule list, the cadence save and the scheduler's
due check (`routes/schedules.py`, `replica/schedules.py`; a scheduled refresh used to be able to
revive a deleted account through `create_run`), muted accounts (`routes/signals.py`), the alert
inbox, notifications, document upload to an account (`documents/service.py`), file runs, the
mail-and-drive account matcher, the Library "already in the workspace" flag, the signals feed of
the open-door API, the operator view-as lists, the member and account counts (`me`, admin, limits),
the Deep dive "quiet lanes" and download-job polling. Deleting is refused while a research job
that has not made its run yet is queued or waiting for credits (it would start and bring the
account back), unless its run already ended. Left as they are on purpose: sites that read for
other accounts' names (forbidden terms, archive PII scrub: a deleted account's name is still
another account), run-internal joins by run id, and ledger/reconcile counts.
Tests: `tests/phase3/test_deleted_account_readers.py`, `tests/parity/test_guards_db.py`.

Found while reading the bundle's writers: a posting title, a person's name, a note or a platform
read from the web that starts with `=` was written by openpyxl as a FORMULA into the client's
workbook (`=HYPERLINK("https://evil.example/x","Click")` as a posting title), and the gate passed
it. The export build writes such cells as strings. Fixed in `parity/excel/*` (`literal_strings`
over the data cells, the workbooks' own formulas untouched); test
`tests/parity/test_excel.py::test_text_read_from_a_page_never_becomes_a_formula`.
Also: uploads listed under a deleted account (`GET /v1/documents?account_id=`) now answer empty.

Orchestration review (`2026-10-30-orchestration-review.md`): checked against what the merge
touches. F9 (ceilings per node) is why the bundle step has its own; F14 (build retried) is why a
second build of a run is a no-op; F8 (run-level verdict hides files) is why the bundle follows the
verdict. Nothing else in the review concerns the merged code.

## 7. Verification

All on this working tree after the last edit, scratch databases of my own (`ai_test_m0`, `ai_test_m1`,
`mig_a`, `mig_b`, `mig_c`, dropped afterwards), `-m "not eval and not live"`, never sourcing `.env`, one
pytest session per database. Other agents were adding `tests/**/test_mutation_kills_*.py` to the tree
at the same time; they are not part of this verification (`--ignore-glob='*test_mutation_kills*'`
for the final run; `ruff` run with the same exclusion).

| Check | Result |
| --- | --- |
| First full suite (before the last fixes) | 5964 passed, 7 skipped, 3 failed: `tests/guardrails/test_config_readers.py` (YAML read in `parity.from_run`/`parity.platforms`), `test_package_lists.py` (`parity` missing from `root_packages` and `source_pkgs`), `test_route_sql.py` (3 raw queries in `routes/parity.py`). All three were mine; fixed (section 4) |
| Failing tests rerun with the guardrails, parity, db, system static and wiring, the delete and leak-wave tests | 318 passed, 6 skipped |
| FINAL full suite, 51 min 58 s | **5815 passed, 7 skipped, 0 failed**, 7 deselected, 1 xfailed. The 7 skips: the large-document opt-in and six parity tests that need the reference workspace, which is not on this machine (gitignored by design). The count is lower than the first run only because the final run leaves out the other agents' mutation tests |
| New tests | `tests/db/test_posting_migrations.py` (4), `tests/parity/test_guards_db.py` (10), `tests/agents/test_parity_hook.py` (6), `tests/phase3/test_deleted_account_readers.py` (2), `tests/parity/test_excel.py` (1), `web/tests/unit/parity-workspace.test.tsx` (1) |
| `ruff check` and `ruff format --check` over `src scripts migrations tests` | clean (1449 files formatted) |
| `mypy` | no issues in 738 source files |
| `lint-imports` | 19 contracts kept, 0 broken |
| `interrogate src` | 100.0 percent |
| Generators `--check` | `gen_loops`, `exports.docs`, `gen_jobs_docs`, `export_openapi`, `gen_web_client`, `wiring_audit`, `gen_env_docs`, `gen_observability`, `gen_stage_map`, `gen_tracker_docs`, `gen_isolation_matrix` (against a migrated database): all current |
| Static scripts | `check_route_catalog`, `check_telemetry`, `check_guarantees` (510 tags, 0 dangling), `check_comments`: clean. `vulture src --min-confidence 80`: clean. `compileall`: ok. `uv lock --check --offline`: consistent |
| Migrations | fresh upgrade to head (one head, `0095`), the staging-state upgrade, idempotent re-apply, two round trips, RLS catalogue, migration lint: section 3 |
| Isolation matrix and RLS tests | inside the full suite (`tests/isolation`, `tests/db`): green |
| Web | `tsc --noEmit`, `eslint .` clean; `vitest` 292 passed in 49 files |
| `render/docx` | `npm ci` + `npm run build`, `npm test` 18 passed, `eslint`, `tsc` clean (it is built in the working tree, ignored by git, so the 40-odd parity render tests ran instead of skipping) |

Not run: the Playwright specs (`web/e2e/*`, they need the compose stack and a browser), a docker image
build (`render/docx` is built in the Dockerfiles; the COPY paths were checked to exist), anything
against GCP.

## 8. Owner decisions

1. Keep both pipelines or retire one. They share the mapping, so the bundle is a second rendering of
   the same rows (a different, owner-checked file shape); it costs 7 s and a second set of stored
   files per research, and it needs Node, LibreOffice and the compiled Word renderer in every
   image (the Dockerfiles install them now). Recommendation: keep `PARITY_BUNDLE` off on staging
   until the images are built and a research is run once with it on; then decide whether the
   delivered files should be rendered from the bundle (one pipeline) or the bundle panel dropped.
2. Turn `PARITY_BUNDLE=on` on staging? Not done. Needs the new images (renderer build in the
   Dockerfiles) and one paid research to see it on real data; the e2e spec (`web/e2e/parity.spec.ts`)
   was not run here (needs the compose stack and a browser).
3. `account_bundles` in the deletion plan: `blocked` (now) or `erase` (needs a new migration granting
   the eraser DELETE and widening `erase_rows`). Same question for stored bundle objects after a
   person's erasure (counsel, D-06).
4. Port the three speed hunks of the other branch's graph (careers beside extraction, jobs beside
   tech, the two builds together)? A few seconds per research; each changes the nodes the D1 wave
   hardened.
5. A failed file-level quality verdict now hides the whole bundle (the export files hide only the
   file). If the owner prefers per-file hiding the bundle document must stop carrying the other
   tables, which the web does not need; say so.
6. Add `deleted_at`, `deleted_by` (and the other owner columns) to the `accounts` ORM model to clear
   `alembic check`, or leave the known drift.
