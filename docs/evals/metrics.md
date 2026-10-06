# Metric definitions (E3) and the statistics note (D-50)

| Metric | Definition (numerator / denominator) | Gate (ARCH initial value) |
| --- | --- | --- |
| coverage | expected attributes found / expected attributes | >= 0.80 |
| high_row_coverage | High tracker rows Done or None / High rows | reported |
| citation_validity | cited claims with a successful stored fetch AND the sentence verbatim in the stored text / cited claims | >= 0.97 |
| precision_grade_a | facts graded Confirmed whose label is Confirmed / facts graded Confirmed | >= 0.95 |
| precision_grade_b | facts graded Probable whose label is Probable / facts graded Probable | >= 0.85 |
| calibration_error | weighted mean of abs(accuracy - mean confidence) over 10 equal-width bins | <= 0.05 |
| unsupported_claim_rate | unsupported (dropped) claims / all claims | primary metric |
| cost_per_run | mean run cost; unknown if any call cost is unknown (never zero) | <= 1.10 x baseline |
| p95_duration_s | nearest-rank 95th percentile of run duration | <= 1.15 x baseline (live, nightly) |
| security_pass_rate | security checks passed / run (canaries, poisoned pages, cross-tenant markers) | 1.0 |

Statistics: Wilson score intervals at 95%; for each primary metric a one-sided paired bootstrap
(cases resampled with replacement, 2,000 resamples, fixed seed) of "candidate worse than
baseline"; Holm step-down correction at family-wise alpha 0.05. The A/A self-test bounds the false
block rate at the configured 5%; seeded regressions must block.
