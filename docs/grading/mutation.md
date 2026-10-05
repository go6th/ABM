# Mutation check (mutmut 3.8.0, 2026-10-02)

Targets: `engine.py`, `product.py`, `primary.py`, `leads.py`. Tests: `test_engine.py`, `test_rules.py`,
`test_lying_generator.py`, `test_rule_boundaries.py` (no database). Run in a throwaway copy with a
`[tool.mutmut]` block (not committed): `paths_to_mutate` as above, `also_copy` including `src`.

| Run | Mutants | Killed | Survived |
|---|---|---|---|
| First | 850 | 717 | 133 |
| After `test_rule_boundaries.py` | 850 | 840 | 10 |

The first run exposed real gaps, now pinned by tests: the last precedence rule mutated to always
return Lead (an input with no engine grade must fall back to Hypothesis); fetch exactly at run start;
the `lineage_unknown` flag's absence; tie-break by group count; the `opaque` default normaliser
(a `None` default would have allowed automatic conflicts for unknown kinds); the 0.5 similarity
boundary (`>=` vs `>`); the Jaccard formula; three queries in one category forged as complete
(`and` vs `or`); exact query records and state resets; exact error messages.

The required examples are caught: swapping max for sum within a lineage group
(`test_worked_examples`, same-group T2 + T4 = 0.75), `>=` for `>` at the thresholds
(`test_f4_exact_boundaries`), and dropping any primary-read condition (`test_primary_read_requires_every_condition`).

## The 10 survivors (all equivalent: no observable behaviour changes)

| Mutant | Change | Why equivalent |
|---|---|---|
| `attribute_class` 5, 8; `normaliser_for` 8, 11 | `split(".", 1)[0]` to `split(".")[0]` or `split(".", 2)[0]` | the first segment is identical |
| `attribute_class` 12 | `cls is None or cls not in half_lives` to `and` | the config validator guarantees every configured class has a half-life, so only `None` can occur, and both forms raise for it |
| `_stronger` 5 | `>` to `>=` | keys equal in every compared field mean identical contributors |
| `quantise` 4, 7, 9 | step `Decimal(2)` instead of `Decimal(1)`; default rounding | `quantize` uses only the exponent; the default context rounding is already half-even |
| `grade_attribute` 62 | pairs compared from `i - 1` instead of `i + 1` | still compares every pair (symmetric); a value is never disjoint from itself |
