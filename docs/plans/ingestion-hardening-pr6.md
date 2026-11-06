# Ingestion hardening PR6: cost and run control (2026-11-04)

**Description.** The upload analysis (step 5) now goes through the existing token guard instead of
running unguarded: `auto_analysis.start` stores `expected_tokens` (the input plus output tokens of
`documents/estimate.py`) on the run, the file-mode graph (`src/documents/graph.py`) gained the same
`token_gate` pause the research graph has (`extract` and `summarise` pause there when the run's
tokens reach `token_guard_multiple` x the estimate; the user's Proceed is `POST /v1/runs/{id}/approve`,
Stop ends the run partial `token_guard_stopped`). No ~1,000-token checkpoint was built (decision Q6).
Analysis runs at most once per document (run key `auto-file:<document id>`, pinned by test, also
across a re-import and `backfill_imports`, which never starts it). The `documents` row records
`embedding_tokens` (added per embedding batch in the call-log transaction) and the analysis run's
`analysis_input_tokens` / `analysis_output_tokens` (set, not added, when the run settles, in
`lifecycle.settle_once`) with `analysis_run_id` (set by `start`). The per-tenant model usage view
`llm_spend_daily` (the one `scripts/cost_report.py` reads) gains embedding batches as surface
`file_embedding`, task kind `embed_passages`, cost as logged (0 for the local embedder); analysis
tokens were already there as surface `research`. Migration `0108_document_tokens`
(down_revision "0106": re-chain after 0107 on merge). No new model calls.

## Behaviour to know

* The guard is off while the research caps are off (`RESEARCH_CAPS=off` / `DEV_UNCAPPED=1`) unless
  `TOKEN_GUARD=on` (owner decision 2026-10-24, `spend_guard.check_tokens`). Staging currently runs
  with the caps off, so the analysis will not pause there until `TOKEN_GUARD=on` is set.
* The estimate counts extraction only; the summary call is not in it (the 3x slack covers it).
* `analysis_*_tokens` are written only for the document whose `analysis_run_id` is the run (the
  upload's own analysis); a file-mode run the user starts by hand does not write them.

## Staging verification

1. Deploy with `TOKEN_GUARD=on` on the worker for the test (otherwise the guard is off).
2. Upload a small workbook bound to an account; after it is indexed:
   `SELECT embedding_tokens, analysis_run_id FROM documents WHERE id = ...` shows tokens > 0 and the run id.
3. Open the account's run; its `run_input->>'expected_tokens'` equals the estimate shown on upload.
4. Upload a large file (estimate small relative to its real use is not needed: lower
   `token_guard_multiple` in `config/spend.yaml` to 1 for the check): the run shows "Waiting for approval";
   Proceed continues it, and `run_input->>'token_guard_ok'` is set.
5. After the run settles: `analysis_input_tokens` and `analysis_output_tokens` equal the run's
   `model_reply_log` sums (served_from = 'model').
6. `SELECT surface, task_kind, sum(input_tokens) FROM llm_spend_daily WHERE tenant_id = ... GROUP BY 1, 2`
   lists `research` and `file_embedding / embed_passages`.
7. Re-run `POST /v1/documents/{id}/...` import or the sweep: still exactly one `auto-file:<id>` run.
