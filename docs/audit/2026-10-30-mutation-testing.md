# Mutation testing, 2026-10-30

Owner request: "full mutation testing", production grade. Everything below was measured on a scratch
copy of the working tree (mutmut 3.8.1, 3 workers, no database, no network, no paid call); nothing
was committed, deployed or deleted, and nothing under `src/` was edited.

## Result

* **35 files mutated, 5,442 mutants, 5,356 killed (including 5 timeouts), 86 survivors, all 86
  equivalent** (each listed with its reason in `docs/testing/mutation_equivalents.txt`).
  Score: **100.0% of the non-equivalent mutants; 98.4% counting every equivalent as a miss**.
  Lowest file: `exports.ooxml` 94.6% and `account_intelligence.safety.prefilter` 95.0% of all mutants
  before the equivalents are set aside. The bar (90% per file, no untested mutant) holds for every file.
* The 19 files of the existing set started at **87.3%** (2,008 of 2,301 killed; 145 survivors, 2
  timeouts counted as kills, 148 mutants of `spend.reconcile` with no test); six files were under
  90% (`spend.approval` 85.5, `exports.workspace` 83.1, `tracker.machine` 88.4, `jobs.counts` 88.6,
  `spend.money` 88.6, `jobs.window` 89.6) and `spend.reconcile` stood at 13.5% of its mutants.
* 16 files were added (the pure and fake-driven logic of the parity release); at first measurement
  858 of their 3,141 mutants (27.3%) were killed by the tests that already existed; now 3,089
  (98.3%), 100% of the non-equivalent. In all, 2,490 mutants went from not killed to killed.
* 14 new test files, 318 tests, all hermetic; they run in 3 seconds. The fast property tests
  pass (`pytest tests -m "not eval and not live" -k "properties or hypothesis"` on the scratch
  database `ai_test_mut`: 127 passed, 6,174 deselected, 110 s).
* Runtime of the full run: **611 seconds** (10 min 11 s; 10.2 mutants per second, 3 workers,
  8 cores) on the scratch copy. The same command in the repository itself
  (`uv run --no-sync --with "mutmut>=3.8,<4" mutmut run --max-children 3`, then
  `scripts/mutation_report.py`) was repeated at the end and gave the same counts (5,442 mutants,
  5,356 killed, 86 equivalent) in 596 seconds; its `mutants/` folder was moved out of the tree.
  `make mutation` itself was not run (it adds `--frozen`, which may sync the shared `.venv` while
  another session is testing).

## How it was run

```bash
# a scratch copy of the tree (src, tests, config, scripts, evals, docs, pyproject), so the merge in
# progress could not change a source mid-run
rsync -a --exclude=__pycache__ src tests config scripts evals docs migrations .claude/skills pyproject.toml uv.lock "$SNAP"/
cd "$SNAP" && UV_PROJECT_ENVIRONMENT="/home/sakib/ABM Generator/.venv" \
  uv run --no-sync --project "/home/sakib/ABM Generator" --with "mutmut>=3.2,<4" mutmut run --max-children 3
```

The repository command is now `make mutation` (see `docs/testing/mutation.md`). The old
`[tool.mutmut]` block also copied `.claude/` (3.9 GB of agent worktrees on this machine) into
every run; `also_copy` now names `.claude/skills/` only (three tests read the skill assets).
`tests_dir` (deprecated, and a duplicate of the selection) was dropped and `paths_to_mutate`
became `source_paths`, so the block needs mutmut 3.8 or newer; the documented command pins
`>=3.8,<4`.

## Baseline: the existing configuration, unchanged

The 19 files of `[tool.mutmut]` as found, run on 2026-10-30 against the sources of that day
(2,301 mutants, about 5 minutes):

| Module | Mutants | Killed | Timeout | Survived | No test | Killed of all | Killed of tested |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `account_intelligence.tracker.completion` | 156 | 152 | 0 | 4 | 0 | 97.4% | 97.4% |
| `account_intelligence.tracker.derive` | 119 | 116 | 0 | 3 | 0 | 97.5% | 97.5% |
| `account_intelligence.tracker.machine` | 138 | 122 | 0 | 16 | 0 | 88.4% | 88.4% |
| `exports.change_note` | 70 | 64 | 0 | 6 | 0 | 91.4% | 91.4% |
| `exports.citations` | 41 | 41 | 0 | 0 | 0 | 100.0% | 100.0% |
| `exports.numbers` | 93 | 87 | 0 | 6 | 0 | 93.5% | 93.5% |
| `exports.policy` | 219 | 214 | 0 | 5 | 0 | 97.7% | 97.7% |
| `exports.workspace` | 301 | 250 | 0 | 51 | 0 | 83.1% | 83.1% |
| `jobs.classify` | 33 | 33 | 0 | 0 | 0 | 100.0% | 100.0% |
| `jobs.counts` | 158 | 140 | 0 | 18 | 0 | 88.6% | 88.6% |
| `jobs.dedupe` | 108 | 107 | 1 | 0 | 0 | 100.0% | 100.0% |
| `jobs.gap` | 42 | 40 | 0 | 2 | 0 | 95.2% | 95.2% |
| `jobs.window` | 48 | 42 | 1 | 5 | 0 | 89.6% | 89.6% |
| `spend.approval` | 83 | 71 | 0 | 12 | 0 | 85.5% | 85.5% |
| `spend.gate` | 90 | 89 | 0 | 1 | 0 | 98.9% | 98.9% |
| `spend.money` | 88 | 78 | 0 | 10 | 0 | 88.6% | 88.6% |
| `spend.priority` | 65 | 62 | 0 | 3 | 0 | 95.4% | 95.4% |
| `spend.reconcile` | 171 | 23 | 0 | 0 | 148 | 13.5% | 100.0% |
| `spend.report` | 278 | 275 | 0 | 3 | 0 | 98.9% | 98.9% |
| **all** | **2301** | **2006** | **2** | **145** | **148** | **87.3%** | **93.3%** |

The 148 mutants without a test are `spend.reconcile.reconcile_run` (SQL; its tests need Postgres).
There was no mutation threshold in `docs/`, the Makefile or CI; the earlier reports
(`docs/spend/mutation.md`, `docs/exports/mutation.md`, `docs/people/mutation.md`,
`docs/phase4/mutation_report.md`) record 90.6% on the pure rule modules (2026-10-04) as the
standing figure.

## Before and after, per file

"Before" is the first measurement: the 19 files of the existing set with their existing tests
(`baseline`), the 16 added files with the existing hermetic tests that already named them (a file
with no hermetic test starts at 0). Killed counts timeouts. "Score now" is killed / (mutants -
equivalent).

| Module | Mutants | Killed before | Before | Killed now | Equivalent | Score now |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `account_intelligence.archive_records` | 284 | 160 | 56.3% | 281 | 3 | 100.0% |
| `account_intelligence.replica.credits` | 911 | 0 | 0.0% | 907 | 4 | 100.0% |
| `account_intelligence.runs.recovery` | 239 | 0 | 0.0% | 238 | 1 | 100.0% |
| `account_intelligence.safety.prefilter` | 180 | 83 | 46.1% | 171 | 9 | 100.0% |
| `account_intelligence.tracker.completion` | 156 | 152 | 97.4% | 156 | 0 | 100.0% |
| `account_intelligence.tracker.derive` | 119 | 116 | 97.5% | 119 | 0 | 100.0% |
| `account_intelligence.tracker.machine` | 138 | 122 | 88.4% | 134 | 4 | 100.0% |
| `account_intelligence.urls` | 129 | 75 | 58.1% | 122 | 7 | 100.0% |
| `agents.production.jobs_carry` | 221 | 0 | 0.0% | 219 | 2 | 100.0% |
| `exports.change_note` | 70 | 64 | 91.4% | 67 | 3 | 100.0% |
| `exports.citations` | 41 | 41 | 100.0% | 41 | 0 | 100.0% |
| `exports.contenturls` | 153 | 47 | 30.7% | 150 | 3 | 100.0% |
| `exports.numbers` | 93 | 87 | 93.5% | 92 | 1 | 100.0% |
| `exports.ooxml` | 352 | 0 | 0.0% | 333 | 19 | 100.0% |
| `exports.policy` | 219 | 214 | 97.7% | 218 | 1 | 100.0% |
| `exports.workspace` | 301 | 250 | 83.1% | 292 | 9 | 100.0% |
| `jobs.classify` | 33 | 33 | 100.0% | 33 | 0 | 100.0% |
| `jobs.counts` | 158 | 140 | 88.6% | 147 | 11 | 100.0% |
| `jobs.dedupe` | 108 | 108 | 100.0% | 108 | 0 | 100.0% |
| `jobs.gap` | 42 | 40 | 95.2% | 42 | 0 | 100.0% |
| `jobs.heuristic` | 93 | 75 | 80.6% | 92 | 1 | 100.0% |
| `jobs.incremental` | 136 | 33 | 24.3% | 135 | 1 | 100.0% |
| `jobs.normalise` | 148 | 122 | 82.4% | 148 | 0 | 100.0% |
| `jobs.snapshot` | 141 | 124 | 87.9% | 139 | 2 | 100.0% |
| `jobs.window` | 48 | 43 | 89.6% | 48 | 0 | 100.0% |
| `people.lane` | 40 | 36 | 90.0% | 40 | 0 | 100.0% |
| `people.level` | 44 | 38 | 86.4% | 44 | 0 | 100.0% |
| `people.priority` | 28 | 26 | 92.9% | 28 | 0 | 100.0% |
| `people.si_firms` | 42 | 39 | 92.9% | 42 | 0 | 100.0% |
| `spend.approval` | 83 | 71 | 85.5% | 83 | 0 | 100.0% |
| `spend.gate` | 90 | 89 | 98.9% | 90 | 0 | 100.0% |
| `spend.money` | 88 | 78 | 88.6% | 86 | 2 | 100.0% |
| `spend.priority` | 65 | 62 | 95.4% | 65 | 0 | 100.0% |
| `spend.reconcile` | 171 | 23 | 13.5% | 171 | 0 | 100.0% |
| `spend.report` | 278 | 275 | 98.9% | 275 | 3 | 100.0% |
| **all** | **5442** | **2866** | **52.7%** | **5356** | **86** | **100.0%** |

The 19 original files before: killed 2,008 (87.3% of 2,301; 93.3% if the 148 mutants without a test
are set aside); survivors 145; timeouts 2; no test 148.

## Mutants killed by each new test file

For each file the mutants of its modules were run again without it (leave-one-out, the other
files kept): the mutants that then survive or lose their test are the ones only that file kills.
Mutants other tests kill too are not counted, so the column is a floor.

| New test file | Tests | Mutated modules | Mutants only this file kills |
| --- | ---: | --- | ---: |
| `tests/jobs/test_mutation_kills_rules.py` | 49 | jobs.heuristic, normalise, snapshot, incremental | 160 of 518 |
| `tests/people/test_mutation_kills_rules.py` | 11 | people.level, lane, priority, si_firms | 15 of 154 |
| `tests/unit/test_mutation_kills_urls_prefilter.py` | 37 | account_intelligence.urls, safety.prefilter | 111 of 309 |
| `tests/replica/test_mutation_kills_records.py` | 17 | account_intelligence.archive_records | 119 of 284 |
| `tests/exports/test_mutation_kills_urls_ooxml.py` | 35 | exports.contenturls, ooxml | 436 of 505 |
| `tests/exports/test_mutation_kills_workspace_rules.py` | 29 | exports.workspace, numbers, change_note, policy | 54 of 683 |
| `tests/spend/test_mutation_kills_messages.py` | 9 | spend.money, gate, priority, approval | 24 of 326 |
| `tests/spend/test_mutation_kills_reconcile.py` | 8 | spend.reconcile | 148 of 171 |
| `tests/tracker/test_mutation_kills_round2.py` | 10 | tracker.machine, derive, completion | 19 of 413 |
| `tests/jobs/test_mutation_kills_counts_window_gap.py` | 9 | jobs.counts, window, gap | 12 of 248 |
| `tests/agents/test_mutation_kills_jobs_carry.py` | 10 | agents.production.jobs_carry | 219 of 221 |
| `tests/runs/test_mutation_kills_recovery.py` | 10 | account_intelligence.runs.recovery | 238 of 239 |
| `tests/replica/test_mutation_kills_credits.py` | 25 | replica.credits (rates, period, estimates) | 153 of 911 |
| `tests/replica/test_mutation_kills_credits_ledger.py` | 59 | replica.credits (reserve, settle, promote, ledger check) | 701 of 911 |

## What the survivors showed (real gaps, not equivalents)

None of the 2,576 mutants that were not killed at first measurement exposed a product bug; each was
a rule the tests did not pin (or a message, a boundary or a default no test read). The notable
ones:

* `account_intelligence.urls.strip_secrets` (removes credentials and secret query parameters from
  every archived address) had no direct test: 33 of the 54 survivors of `account_intelligence.urls`. Now pinned (credentials with
  an `@` in the password, case of a key, a pair without `=`, a value that looks like a secret
  name).
* `exports.workspace` release files (`data/releases/`): the folder check, the sort order of the
  members, the entry time, the compression level and the 80-character cut of a problem message were
  all unchecked; the workspace zip is a client deliverable.
* `safety.prefilter.needles` (the skip test of the leak scan) was only compared against the plain
  scan, which stays equal when the filter never skips: 97 survivors. The needles, the folding and
  the slicing of a large text are now pinned.
* `spend.reconcile.reconcile_run` had no hermetic test (148 mutants): the cap comparison, the
  connector sum, the stale reservation and the write are now driven by a scripted session.
* `jobs.incremental.previous_delivered_run` (which stored research may be the base of the next):
  the safe-base rules (converged and complete pull, same lanes and lexicon) had no hermetic test.
* `replica.credits` (the credit ledger): the pool, reserve, settle, promote, settle-run and
  the nightly check had Postgres tests only; they are now driven by a scripted session and the
  `ledger_problems` rules are covered one by one.
* Refusal messages of money, approval, gate, priority, counts and the tracker were asserted by a
  fragment; they are now asserted whole (an operator reads them).
* The mutation run itself had two traps (the `lru_cache` of `needles`, a copy of 3.9 GB of agent
  worktrees); both are fixed and written in `docs/testing/mutation.md`.

## The 86 equivalent mutants

By reason (the ids and the reasons are in `docs/testing/mutation_equivalents.txt`):

* 15: `zipfile.writestr` takes the compression method and level passed, resets a falsy
  `external_attr` to `0o600 << 16`, and its default level is 6 (`workspace.build_workspace`,
  `ooxml._pack`, `ooxml.strip_cached_values`). The one mutant that did change the bytes (level 7)
  is killed by comparing a member's raw deflate stream with level 6 of `zlib`.
* 11: guards the typed models make unreachable (`jobs.counts`, marked `pragma: no cover` in the
  module): class totals, sentence counts, a platform block larger than the distinct block.
* 6 + 2: codec names in upper case (`"UTF-8"`), and the strip set gaining the letter `X`.
* 7: slicing of a text larger than 4 MB in `safety.prefilter` (same answer, different memory use or
  number of passes) and the empty index.
* 4: `ooxml._sheet_parts` parser hardening (no entity expansion): sheet names are attributes,
  expanded either way, and no external resource is reachable from a test.
* 4 + 3 + 3 + 3: falsy defaults (`None` against `[]` or `False`), `credits.estimate_resume` at
  nothing planned or everything left, `change_note._rows` `strict=` after a length check, and an
  empty address cleaned to `None` instead of skipped.
* the rest (one or two each): `str.split` maxsplit where only the first element is read, `rfind`
  with start `None`, the status literal fallback, an advisory of zero, a one-character path, a
  codec, a threshold argument that is unused, and the slice count read only when resumed.

## Not mutated, and why

* `replica.current` (`newest_only` and four SQL fragments): mutmut makes no mutant of an f-string
  or a module-level constant. A plain test pins the clause text
  (`tests/replica/test_mutation_kills_records.py`).
* Files with mostly database logic and no injectable session (`jobs.repo`, `jobs.adapters`,
  `exports.readers.*`): their tests need Postgres, which a per-mutant run cannot afford. They stay
  in the normal suite.
* The renderers (`render/docx`, Vitest) and the web app: a TypeScript mutation run (Stryker) is a
  separate decision.

## Files

Edited: `pyproject.toml` (`[tool.mutmut]` only), `Makefile` (`mutation`, `mutation-report`),
`docs/guide/05-running-and-deploying.md` (one paragraph), `scripts/README.md` (one row).
New: `scripts/mutation_report.py`, `docs/testing/mutation.md`, `docs/testing/mutation_equivalents.txt`,
this report, `tests/replica/credits_sql.py` (the statements of `replica.credits`, copied so a change
fails a test) and the 14 test files above.

## Cautions for the merge

* The 35 files were unchanged between my snapshot and the end of the run. If a mutated module
  changes, rerun `make mutation`; the equivalents file names ids that move when a function changes
  (the report says which listed ids no longer exist).
* `tests/replica/credits_sql.py` and the scripted sessions pin the SQL of `replica.credits`,
  `spend.reconcile` and `runs.recovery`: an edit to one of those statements fails a test on
  purpose; copy the new text in.
* `make mutation` writes `mutants/` (about 90 MB, git-ignored) at the repository root.
