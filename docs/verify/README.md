# Verification protocol and finding codes (D-49)

When: inside `distill_grade`, after the support judge and the fact writes (`src/verify/stage.py`).

Protocol for each Confirmed fact (High rows first, at most `max_claims_per_run`):
1. Take the run's own stored source the evidence cites (`source_ref`); never refetch.
2. Ask the judge tier (task kind `source_reread`, prompt `src/verify/prompts/source_reread.v1.md`)
   a question built from the attribute key only, with the source text (bounded). Fresh context, no
   tools, never the claimed value.
3. Code decides the outcome (`verify.claims.judge_reread`).
4. Every outcome is a `verifier_findings` row; every non-confirmed outcome supersedes the fact with
   the G12 cap (new row, grade event by `verifier`).

| Outcome | Meaning | Action |
| --- | --- | --- |
| `confirmed` | quote in the source, answer in the quote, normalised answer equals the claim | keep |
| `value_mismatch` | a real quote but a different value | downgrade |
| `not_found` | no answer, no quote, a quote not in the source, or an answer not in its quote | downgrade |
| `unreadable` | the stored source could not be read | downgrade |
| `unavailable` | the verifier failed (model error, schema, budget, over the claim cap) | downgrade |
| `not_independent` | the verifier is of the generator's family (R6) | downgrade |
| `figure_ok` / `figure_mismatch` / `figure_unregistered` | a narrative number against the registry for its cited rows | flag |
| `supported` / `overstated` / `unsupported` / `contradicted` | narrative sentence verdicts | keep / rephrase by template / remove / remove |

Client output shows only the final grade; findings are internal.
