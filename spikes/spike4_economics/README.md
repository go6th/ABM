# Spike 4 — Broker economics on 20 accounts (T13)

**Pass criteria:**
- 20 accounts run through the 5 brokers behind a metering shim
- cost per run within the planned cap ($6.00 standard depth)
- coverage of expected attributes ≥ 0.80
- people-source decision recorded (BitScale vs Sumble)

## Run

```bash
.venv/bin/python spikes/spike4_economics/run.py --accounts 20 --json-out /tmp/spike4.json
```

`dry_run` (default) uses deterministic synthetic accounts and list-price estimates — no
network, no spend. `live` is wired to the same metering shim and will refuse to run until
the real broker adapters land in Phase 1 (T44–T46) **and** the T6 legal clearance is on
file; this prevents accidental spend and unlicensed resale.

Measured values go to `docs/phase0/broker_economics.md` and update
`docs/adr/cost_estimate_phase1.md`.
