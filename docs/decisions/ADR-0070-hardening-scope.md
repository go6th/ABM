# ADR-0070: What hardening can prove here (D-80)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

ARCH sets RPO under 60 s and RTO 30 minutes as targets proven by a game-day; no GCP is reachable
here.

## Decision

* Hermetic: `ops/dr/restore_drill.py` (base backup with streamed WAL, replay in a second
  instance, invariants, isolation suite on a restored copy); a scripted kill and recovery test; a
  report-only load profile against stub providers; a generated status page (publishing
  NotConfigured).
* Real (Blocked until staging): Cloud SQL restore and PITR, failover, regional DR, a k6 run.

## Consequences

Measured rehearsal: `docs/phase4/restore_drill_report.json`, `docs/phase4/load_report.md`.
