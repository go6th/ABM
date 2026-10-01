# T12 — Prompt-cache measurement results

Status: **dry-run measured; live values pending** (live run requires SPIKE3_API_KEY in `.env`).

## Dry-run evidence (2026-10-01)

Command: `python spikes/spike3_cache/run.py --calls 10` (deterministic simulator)

- calls 2..10 served from the stable prefix: ratio = 1.00 per call
- mean ratio (call 2+): **1.00**, min: **1.00** → PASS (≥ 0.70)
- with `--invalidate` (timestamp-like prefix change on calls 4 and 7):
  min ratio **0.00** → FAIL, confirming the harness detects silent invalidation

## Live measurement (fill when run)

| Item | Value |
|------|-------|
| Provider / model | _pending_ |
| Calls | 10 |
| Mean cache-read ratio (call 2+) | _pending_ |
| Min ratio (call 2+) | _pending_ |
| Gap check (≥ 6 min pause) | _pending_ |
| PASS / FAIL | _pending_ |

## Cost model impact

`docs/adr/cost_estimate_phase1.md` "Measured values" table is updated from this spike.
Until then the cost model keeps the Spec 16.2 sensitivity band:
0% ratio = $1.62/run (+45%), 75% = $1.12/run, 90% = $1.02/run (−9%).
