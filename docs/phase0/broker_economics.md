# T13 — Broker economics results

Status: **dry-run measured; live values pending** (live run blocked on T6 legal clearance
and Phase 1 adapters T44–T46 — by design, to prevent unlicensed spend).

## Dry-run evidence (2026-10-01)

Command: `python spikes/spike4_economics/run.py --accounts 20` (deterministic shim,
list-price estimates)

| Metric | Value | Target |
|--------|-------|--------|
| Accounts | 20 | 20 |
| Mean cost per run | $2.99 | ≤ $6.00 cap |
| Max cost per run | $3.52 | ≤ $6.00 cap |
| news group | $0.40 / run | — |
| jobs group | $0.32 / run | — |
| people group | $1.33 / run | — |
| tech group | $0.94 / run | — |
| Coverage of expected attributes | 0.85 | ≥ 0.80 |
| People decision (dry-run) | BitScale (fill 0.90 @ $0.74) vs Sumble (fill 0.90 @ $0.68) | — |

## Live measurement (fill when adapters + clearance land)

| Item | Value |
|------|-------|
| Broker contract rates (V7) | _pending_ |
| Mean cost per standard run | _pending_ |
| Coverage of expected attributes | _pending_ |
| People-source decision with fill-rate evidence | _pending_ |
| PASS / FAIL | _pending_ |

## Notes

- The dry-run decision logic splits by attribute when neither people source alone covers:
  the recorded rule is "BitScale + Sumble split" with fill rates and cost per usable senior
  person as the deciding evidence (T13 exit criterion).
- Live numbers replace the $4.50 MVP broker assumption in `docs/adr/cost_estimate_phase1.md`.
