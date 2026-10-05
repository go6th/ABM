# Reference findings F1 to F5 (scripts/grading_reference.py)

The skill's reference is the behavioural spec; its worked examples all pass here
(`tests/grading/test_engine.py::test_worked_examples`, `test_reference_selftest_runs_green`, and a
300-case randomised parity test). These weaknesses were each shown failing against the reference first
and fixed in `src/account_intelligence/grading/engine.py`.

| # | Weakness in the reference | Proof (test) | Fix |
|---|---|---|---|
| F1 | Best value among equal confidences depends on input order (`max` over a dict in insertion order) | `test_f1_reference_tie_break_depends_on_input_order` | Rank by confidence, then lineage-group count, then normalised value; groups processed sorted; within a group ties prefer the better tier |
| F2 | Unknown tier or class raises `KeyError`; a naive datetime raises `TypeError` | `test_f2_reference_raises_untyped_errors_and_ours_are_typed` | `UnknownTier`, `UnknownAttributeClass`, `NaiveDatetime` |
| F3 | Age measured from retrieval: a three-year-old article fetched today scores 0.70, grade B | `test_f3_reference_scores_an_old_article_as_fresh_and_ours_ages_it`, `test_f3_unknown_date_cannot_support_grade_a` | Evidence carries a date and its kind (observed, published, unknown); unknown cannot carry A (ADR-0011) |
| F4 | Float comparison at the thresholds (fresh T2 + fresh T4 is 0.85 only up to float error) | `test_f4_exact_boundaries` | Confidence quantised to 9 decimals and compared as `Decimal` against thresholds parsed from config |
| F5 | `now` defaults to the wall clock | `test_f5_now_is_required` | `now` is a required, timezone-aware argument |

Intentional differences in the randomised parity test are limited to F1 (ties) and F4 (exact
boundaries); F3 does not arise there because both sides use the same dates.
