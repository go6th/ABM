# ADR-0045: routing changes are governed (D-51)

- Status: Proposed (D-51)
- Date: 2026-10-06
- Deciders: TODO

## Decision
Every version of `config/model_routing.yaml` is recorded in `docs/routing/DECISIONS.md` with its
sha256 and either a benchmark report (`benchmark: benchmark-<id>`) with a human sign-off or
`status: proposal-unapplied`. `scripts/check_routing_governance.py` runs in the eval workflow; the
container refuses `MODEL_ROUTER=on` while `benchmark_report` is null.
