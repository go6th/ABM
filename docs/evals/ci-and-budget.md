# Eval CI and budget runbook (D-50)

* Workflow: `.github/workflows/eval.yml`. Steps: routing governance, contamination, harness
  self-tests, replay eval (writes `eval-out/eval-report.md` and `.json`), the gate (no override).
* Required modes: pull request `replay`; prompt, routing, agent or grading change `replay` and
  `live`; nightly all three. `live` is not configured (no budget, no Workload Identity binding), so
  it reports `not_run` and blocks such changes until it is.
* Expected state today: the gate BLOCKS on thresholds because the labels are synthetic. Make
  `eval` a required status check in branch protection only after real golden accounts land
  (V-CI-2); until then treat it as advisory.
* Live budget: `config/evals.yaml` `live_budget_usd` is 0.00 (off). Setting it needs the owner's
  decision; a live run stops at the cap and fails closed (E9).
* A red gate: read `gate.md`; a `mode ...: not_run` line means a required mode did not run; a
  `significant drop` line names the metric; a `flaky` line means identical input disagreed.
