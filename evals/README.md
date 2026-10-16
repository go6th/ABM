# `evals/`: golden data and offline benchmarks

The agent evaluation inputs and the offline harnesses. The eval **engine** is the `src/evalgate/`
package; this folder holds the data it scores against and the Phase 0 benchmark scripts. The
nightly `eval.yml` workflow and the `eval` pytest marker use it; the regular test run does not.

| Path | What it is |
| --- | --- |
| `golden/accounts/*.yaml` | Golden accounts: analyst-reviewed expected outcomes for whole runs (synthetic today) |
| `golden/labels/*.yaml` | Labels for the contamination check |
| `golden/synthetic.jsonl` | Synthetic eval cases |
| `golden_accounts.py` | Loads and validates the golden set |
| `grading_calibration.py` | Offline calibration of the grading engine against labelled findings |
| `model_benchmark.py` | One model for everything versus a split by step; writes `docs/benchmarks/model-split.md` |
| `people_compare.py` | People source comparison protocol; writes `docs/people-source-comparison.md` |

Policy and budget for the gate: `docs/evals/ci-and-budget.md`. Thresholds: `config/evals.yaml`.
