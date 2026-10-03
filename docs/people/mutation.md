# People pipeline mutation check (mutmut 3.8, 2026-10-03)

Modules: `merge.py`, `scope.py`, `exclusion.py`, `counts.py`, `tenure.py` (1007 mutants), run in
a scratch copy with the people tests (pure, counts, rubric, golden, lying, edges, mutation kills).

| Run | Killed | Timeout | Survived |
|---|---|---|---|
| 1 (before `tests/people/test_mutation_kills.py`) | 848 | 2 | 157 |
| 2 (with the kill tests) | 941 | 0 | 66 |

After run 2, five more survivors were real gaps and got tests (bucket, certification and alumni
counts that overwrote instead of accumulating; hostile posts subtracted; the merge threshold not
passed in `stable_plan`). Those tests were added after run 2; a third run was not made.

The remaining survivors were reviewed and are equivalent or defensive:

- error message text of `MergeError` and `InvariantViolation` (the type is what callers catch);
- the forward or reversed order of the merge stability re-check (the plan is order independent,
  so both orders give the same plan; the property test proves it);
- the defensive invariants in `counts.py` that are identities by construction (marked
  `pragma: no cover`), and the "duplicate key" and pointer checks' messages;
- evidence length caps one character longer (`253` vs `254`, `200` vs `201`) on values that
  never reach the cap in the corpus;
- the empty-string placeholder used as a counter key for a value that is never missing.
