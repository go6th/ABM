# ADR-0033: advisory breakdown and scope (D-29)

- Status: Proposed (D-29 open)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Decision (proposal)

- The breakdown by provider and category (low, expected, high per line) is **advisory**: the spend
  report shows each provider's variance against it and flags a variance above
  `variance_threshold_pct` (25); it never blocks a call. The total is the only hard limit.
- The cap is sized as the expected total plus `headroom_pct` (10), rounded up to
  `rounding_step_usd` (0.05); each call reserves its upper-bound estimate (K4). The minimum viable
  cap (mandatory operations at their low bound) and the rows Blocked at each lower cap are shown.
- Operations with unknown cost get a bounded sample line (`sample_size` calls held at
  `sample_hold_usd`); further calls need a new approval (K5).
- Model (LLM) spend is reported as an informational line, not counted against the cap (ARCH keeps
  a separate token cap). The per-tenant daily cap and the monthly refresh budget are not built
  (no values in the sources); `CapPolicyPort` is the hook.

## Consequences

Evidence: `tests/spend/test_core.py` (proposal), `test_oracles.py` (variance and flag by hand).
