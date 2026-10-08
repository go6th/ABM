# ADR-0091: ABM run modes (D-102)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The skill's two phases can be requested separately; the guide (S9, S10) establishes Phase 1 (pull, no interpretation) and Phase 2 (sense check, then build). A model or free-text parsing must never choose what runs or spends.

## Decision

A run has `mode` = `full` (default when absent), `pull` or `build`, set only by a typed field (form, `POST /v1/runs` `mode`, scheduler). Modes are slices of one graph (`agents/graph.py`), not new run states: the Phase 2 slice always reads the manifest from the store, so `full` equals `pull` then `build`. A build is a child of a pull (`parent_run_id`, same-tenant composite FK, migration 0041), pinned to the parent's skill config version unless a newer one is chosen and recorded; a missing or unusable pull fails fast with `no_pulled_data` and never runs Phase 1; stale pulls need acknowledgement. Rejected: inferring mode from the request text; separate code paths per mode.

## Consequences

Proven by `tests/agents/test_modes.py` (full equals pull then build on three oracle worlds) and `tests/phase3/test_abm_modes_api.py`. The ABM graph is not yet the production runner's graph (OI-ABM-1). An older parent config version cannot be loaded, so the build must choose the current version explicitly (`config_version_unavailable`).
