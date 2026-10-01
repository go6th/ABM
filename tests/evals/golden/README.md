# Golden accounts

Regression baselines for the pipeline (roadmap section 6). Each file is one account, validated by `app/golden/schema.py`; the JSON schema `golden_account.schema.json` is generated from that model and a test fails if the two drift.

## The files here are SYNTHETIC placeholders

`synthetic_alpha.json`, `synthetic_beta.json` and `synthetic_gamma.json` exist only so tests and the benchmark harnesses run. They describe made-up companies on `.example` domains, every source text starts with "SYNTHETIC TEXT", and each file has `"synthetic": true`. They contain no real company facts and are not evidence of anything.

Code that produces evidence (parity reports, decision log entries) loads golden accounts with `allow_synthetic=False`, which refuses these files.

## What the analyst must do

Replace the placeholders with **three to five real, reviewed September runs** (roadmap section 18, week 1):

1. Pick the accounts from the nine September runs.
2. For each, write one file `<short_name>.json` with:
   - `account`: name, domain, industry, primary lane.
   - `sources`: every source the known facts rely on, with its URL, date, whether it is primary, the coverage rows it serves, and the text as read.
   - `known_facts`: facts the analyst confirms are correct, each with grade, URL, the exact supporting sentence (must appear in the source text), reasoning for Probable and a confirming question for Hypothesis.
   - `expected_coverage`: the status each coverage row reached in the manual run. Every High row is required.
   - `expected_people`: total people in the final list, priority A count, people excluded, and optionally counts by lane (which must sum to the total).
   - `review`: `reviewed_by`, `reviewed_on` and `source_run`. A real account without a reviewer and date is rejected.
   - `"synthetic": false`.
3. Delete the synthetic files once at least three real ones are in place, and update the expected numbers in `tests/app/test_golden.py` and `tests/app/test_benchmarks.py` that refer to them.
4. Run `make app-check`.

These files contain names of real companies and possibly people. Keep the repository private, and follow `docs/data_handling.md`.
