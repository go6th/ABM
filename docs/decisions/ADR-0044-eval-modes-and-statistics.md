# ADR-0044: eval modes, metrics and statistics (D-50)

- Status: Proposed (D-50)
- Date: 2026-10-06
- Deciders: TODO

## Decision
`replay` (recorded model outputs and provider cassettes through the real pipeline; every PR),
`live` (real models; required on prompt, routing, agent or grading changes; needs a budget and
Workload Identity: reported `not_run` and blocking until configured), `nightly` (schedule).
Metrics are exact code definitions from stored rows (`evalgate.metrics`); Wilson intervals; a
seeded one-sided paired bootstrap per primary metric; Holm correction at family-wise alpha 0.05;
k repeats; flaky metrics block. ARCH thresholds are the initial values. Baselines are immutable
(new files or rows). The package is `evalgate` because the root `evals` package exists.

## Consequences
Synthetic labels never pass the thresholds, so the eval check stays red until analyst-labelled
golden accounts arrive; make it a required check only then (docs/evals/ci-and-budget.md).
