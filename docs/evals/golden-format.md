# Golden label format and labelling guide (E2)

Files: `evals/golden/labels/<account>.yaml` (format in `src/evalgate/golden.py`). Each label:
`attribute_key`, `value` (the analyst's verified value), `grade` (the weakest grade the analyst
accepts), `captured_on` (date read), `source_url` (where the analyst read it), `analyst` (an opaque
id like `a-7f3c`, never a name), `split` (`dev` or `holdout`). Each file: `account`, `synthetic`,
`expected_attributes`, `high_rows`.

For analysts: read the primary source yourself; copy the value exactly as stated; record the date
you read it; use `holdout` for about a third of labels and never tune prompts on them; re-verify
every label each quarter (labels older than 92 days are excluded and counted). Never put a label
value in a prompt or example (the contamination check fails the build).

What the owner must supply: 50 analyst-labelled accounts (growing to 200), stratified by size,
industry and region, each with stored source snapshots and consent.
