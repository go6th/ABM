# ADR-0031: the cap unit (D-27)

- Status: Proposed (D-27 open; conflict C13)
- Date: 2026-10-04
- Deciders: TODO (product owner)

## Context

The PRD speaks of one credit cap; ARCH sets a $5.00 hard broker cap. No provider publishes a
credit-to-USD rate for the account's plan (V7), so credits cannot be added across providers.

## Decision (proposal)

One number per run in **USD**, held in code as integer micro-dollars (`spend.money`, no float,
costs round up). Provider credits are recorded per call in their own `Decimal` column and never
summed into the cap. A cap may be entered in credits only for a provider with a sourced
`credit_usd_rate` in `config/spend.yaml` (none today). The skill's $5.00 default is shown beside
every proposal and is never used as the cap without an approval.

## Consequences

Credit-priced calls are held at the labelled `unknown_cost_reserve_usd` placeholder until a rate
is supplied. Evidence: `tests/spend/test_core.py`, `test_properties.py`, `test_mutation_kills.py`.
