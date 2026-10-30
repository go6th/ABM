# Mutation testing

*Written 2026-10-30. Evidence and method: [`docs/audit/2026-10-30-mutation-testing.md`](../audit/2026-10-30-mutation-testing.md).
Earlier per-area runs (October 2026): [`spend`](../spend/mutation.md), [`exports`](../exports/mutation.md),
[`people`](../people/mutation.md), [`phase 3`](../phase3/mutation_report.md), [`phase 4`](../phase4/mutation_report.md).*

A mutation run changes the code one small step at a time (a `<` becomes `<=`, a constant is
altered, a message is edited, a branch is inverted) and runs the tests. A test suite that still
passes has not noticed the change: the **mutant survived**. A survivor is either a missing test or
a change that cannot be observed (an *equivalent* mutant). The coverage figure says which lines
ran; the mutation score says whether the tests would notice if those lines were wrong.

## The bar

**90 percent of the non-equivalent mutants of every mutated file are killed**, and no mutant is
left without a test. A timeout counts as a kill (the mutant hung the tests). An equivalent mutant
is listed with its reason in [`mutation_equivalents.txt`](mutation_equivalents.txt); the gate
reads that file. Nothing else is excluded.

## Run it

```bash
make mutation          # mutmut run (3 workers), then the bar; about 10 minutes on 8 cores
make mutation-report   # only the per-module table and the bar, from the last run
```

The equivalent commands, which `make` wraps:

```bash
uv run --frozen --with "mutmut>=3.8,<4" mutmut run --max-children 3
uv run --frozen python scripts/mutation_report.py            # exit 1 under the bar
uv run --frozen --with "mutmut>=3.8,<4" mutmut results      # the survivors, by name
uv run --frozen --with "mutmut>=3.8,<4" mutmut show <id>    # the diff of one mutant
```

mutmut copies the sources, `tests/`, `config/`, `scripts/`, `docs/`, `evals/` and
`.claude/skills/` into `mutants/` (ignored by git) and runs the selected tests there; the repository
itself is not touched. Close other test runs first: three workers plus a Postgres test session
slow each other. Delete `mutants/` (or `make clean`) to start from scratch; mutmut resumes from
what it recorded otherwise.

## What is mutated, and with which tests

The configuration is `[tool.mutmut]` in `pyproject.toml`.

* `source_paths`: the files mutated. Every one is pure logic, or takes its database session,
  transaction or queue as an argument. mutmut mutates whole files, so a file that mixes both is
  only included when its database part can be driven by a fake (see below).
* `pytest_add_cli_args_test_selection`: the **only** tests run per mutant. They must be hermetic:
  no database, no network, no model. A test that needs Postgres fails the clean run and mutmut
  stops; the Postgres tests of the same behaviour stay in the normal suite.
* `also_copy`: the directories the tests read at run time.

Add a module: put the file in `source_paths`, its hermetic tests in the selection, run
`make mutation`, read the survivors with `mutmut results`, and write the test for each one that
changes behaviour. List one that does not in `mutation_equivalents.txt` with the reason. The kill
tests of the parity release are the `test_mutation_kills_*.py` files named in `pyproject.toml`;
each test names the behaviour a mutant had changed unnoticed.

### Things that cost a day the first time

* **`functools.lru_cache`.** mutmut collects stats in the process it later forks for every mutant,
  so a cached function (`safety.prefilter.needles`) is answered from the cache and a mutant of
  the helpers behind it never runs. The tests of such a function clear the cache first
  (`needles.cache_clear()` in an autouse fixture).
* **A fake session pins the statements.** The scripted sessions of the `credits`, `reconcile`
  and `recovery` kill tests answer a statement by its exact text (`tests/replica/credits_sql.py`
  holds the texts of `replica.credits`). A change to one of those statements fails a test and
  is meant to: copy the new text into the file after reading the change.
* **Strings of messages are behaviour.** An error message is read by an operator and a log
  search; the kill tests assert the exact text, not a fragment.
* **Defence in depth.** An explicit check that a stricter check also covers (the name checks of
  `workspace.safe_name` behind the strict name pattern) is killed by switching the strict check
  off in the test (`monkeypatch.setattr(W, "_SAFE", ...)`), not listed as equivalent.

## The score (2026-10-30, mutmut 3.8.1, runtime about 10 minutes with 3 workers)

"Before" is the first measurement of the module (the 19 modules of the earlier runs: 2026-10-30
baseline of the existing tests; the others: the existing hermetic tests, or none). "Killed now"
counts timeouts as kills.

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

Reading the last column: killed / (mutants - equivalent). The 86 equivalents are in
[`mutation_equivalents.txt`](mutation_equivalents.txt), grouped by reason in the audit report.

## What is not covered

Mutation testing here covers pure logic and logic that can be driven by fakes. It does not cover
SQL semantics (the statements are pinned by text, their effect is checked by the Postgres tests),
the model prompts and the quality of a graded finding (the evals), the renderers (`render/docx`
has its own Vitest suite) or the web screens (Playwright). A module with mostly database logic
and no fake-driven test is not in the set; `spend.reconcile` and `replica.credits` are in it
because their sessions are injected.
