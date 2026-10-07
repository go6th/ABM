# Seller usefulness report: <tenant> <window>

Definitions are **proposals** (D-68, `config/pilot.yaml`), to be confirmed by the product owner.

| Measure | Used | Base | Rate | Small base? |
|---------|------|------|------|-------------|
| First-call questions used (marked or copied) / delivered in the newest report version | | | | |
| Confirmed facts referenced (opened, noted, exported) / current public Confirmed facts | | | | |

Window: the last `metric.window_days` days. Source: `GET /v1/usefulness/metric`.

Rule: when "Small base?" is yes, report the counts only ("3 of 7"), not a percentage, and draw no
conclusion. Never combine tenants into one rate in a customer-facing report; the operator
aggregate (`GET /operator/v1/pilot/aggregate`) is counts per tenant, for 6th only.

## Feedback by reason

| Verdict | Reason | Count |
|---------|--------|-------|
| | | |

## Notes

Known gaps: the client app does not yet show first-call questions on screen (they are in the
Word Account Summary), so "used" counts only what the API receives.
