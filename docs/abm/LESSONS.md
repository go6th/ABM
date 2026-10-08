# Lessons as code (D-99)

**Status: INCOMPLETE.** The archive's `lessons.md` (guide S8: dated sections 2026-09-20 to
2026-09-29) is absent, so `config/lessons.yaml` lists no lesson. None was written from memory.
`python scripts/abm_lessons.py check` exits 3 (INCOMPLETE) until the archive is loaded; it never
reports OK on an empty list.

## How a lesson becomes enforced

1. `python scripts/abm_lessons.py generate <archive>/references/lessons.md` prints one YAML entry
   per bullet (`id L-YYYYMMDD-n`, `source lessons.md:<line>`, `rule`, `enforcement: process`) and a
   stub test module. Assumed format: `## YYYY-MM-DD` headings with `-` or `*` bullets ([VERIFY]
   against the archive, OI-ABM-2).
2. For each lesson choose the guard: `code` (a guard id G1..G17), `prompt` (the agent whose
   `prompt.md` carries the rule tagged `[L-...]`) or `process` (no automatable guard: flagged and
   failing the check until the owner accepts it).
3. Replace the stub test with a test of that guard (a lying scenario where possible).
4. `python scripts/abm_lessons.py check --run-tests` must exit 0. New lessons arrive by pull
   request with their test.

## Lessons table

| Id | Date | Source | Rule | Enforcement | Guard | Test |
|---|---|---|---|---|---|---|
| (none: archive absent) | | | | | | |
