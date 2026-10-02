# Golden account input and loader

The test loader accepts the versioned JSON shape in `tests/fixtures/synthetic/golden_accounts.json`. That file is explicitly synthetic and is usable only when a test opts into `allow_synthetic=True`; it is not a benchmark result or a real golden set.

Real inputs must have `dataset_type: analyst_reviewed_real`, three to five accounts, one ISO capture date per account, analyst reviewer and review date, and known-correct facts with product grade, stable source code, source URL, quote and capture date. Before adding them, supply:

1. Three to five September run output bundles with source snapshots and stable account IDs.
2. Analyst-reviewed known-correct facts and grades for each account, including reviewer identity and review date.
3. The exact source evidence/quotes and expected coverage labels for each captured run.
4. Approval to store those materials in this repository; otherwise keep real data in an approved, access-controlled store and run the loader against a mounted file.

No real account data, analyst review or actual golden account is present in this repository. Synthetic tests prove only that the format and loader work.
