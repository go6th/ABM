# Phase 2 reference: mutation validation

## Why, not just what

A suite can reach 100% line coverage with zero meaningful assertions. The mutation score is the only metric that grades the assertions: kill rate over generated mutants. Use these framework configs, then remediate survivors by hypothesis → targeted test → rerun.

## Python: mutmut 3

Configure in `pyproject.toml`. Scope `paths_to_mutate` to the pure decision modules (rule boundaries, money, counts, state machines) — mutating I/O glue wastes hours and proves little. Pin the test files that exercise those paths in `tests_dir`/test selection so each mutant runs seconds, not the whole suite.

```toml
[tool.mutmut]
paths_to_mutate = ["src/pkg/rules.py", "src/pkg/money.py"]
tests_dir = ["tests/test_rules.py"]
```

Run: `uv run --with "mutmut>=3.2,<4" mutmut run` (or `mutmut run` in the project's venv). Then:

- `mutmut results` — summary; `mutmut show <id>` — the diff of a survivor.
- **mutmut 3 has no break-threshold setting.** Gate in CI by parsing results:

```bash
uv run --with "mutmut>=3.2,<4" mutmut run || exit 1
TOTAL=$(mutmut results 2>/dev/null | grep -oE '[0-9]+' | head -1)  # adapt to your reporter
# Prefer the JSON/JUnit export where available; fail the job when killed/total < 0.80.
```

The `mutants/` working directory is a cache: commit it to `.gitignore`, never to the repo. After source changes, mutmut re-checks by hash and resumes.

## JavaScript / TypeScript: Stryker

`stryker.conf.json` — incremental runs keep the feedback loop fast; `break` is the CI gate:

```json
{
  "$schema": "./node_modules/@stryker-mutator/core/schema/stryker-schema.json",
  "testRunner": "vitest",
  "reporters": ["html", "clear-text", "progress"],
  "coverageAnalysis": "perTest",
  "mutate": ["src/**/*.ts", "!src/**/*.spec.ts", "!src/**/index.ts"],
  "thresholds": { "high": 95, "low": 85, "break": 80 },
  "incremental": true,
  "concurrency": 4
}
```

Stryker exits non-zero below `break`; wire that straight into CI.

## Survivor remediation loop

For each surviving mutant:

1. **Hypothesis:** name the behavior the mutant changes and why no existing test observes it (untested boundary, weakened assertion, assertion on a copy instead of the output).
2. **Targeted test:** one test that fails on the mutant and passes on the original. Prefer exact-output oracles over loose matchers — loose matchers are how mutants survive in the first place.
3. **Verify:** rerun the single mutant (`mutmut show <id>` / `stryker run --mutants <id>` equivalent), then the full mutation run for the score.
4. **Equivalent mutants:** if no input distinguishes mutant from original, document why (e.g., the mutation only alters an unreachable branch or a log string). Count it out of the denominator explicitly; do not silently accept it.

Known survivor factories to design against: `return None` → `return x`, boundary off-by-one (`<` → `<=`), condition negation, and boolean literal flips on defaults.
