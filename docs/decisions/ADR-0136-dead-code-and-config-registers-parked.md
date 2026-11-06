# ADR-0136: dead packages, orphan routes and unread config are parked; config/ holds only loaded files

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt F, tasks 1, 4 and 6)

## Context

The 2026-11-03 audit found code with no importer in `src/`, routes no screen calls
(`docs/audits/orphan-routes.md`, deletion list), configuration files no code reads, and a
documentation guide (`docs/guide/`) that had drifted from the code. Owner rule (2026-10-16):
removed code is moved into `unwanted-things/`, never deleted.

## Decision

1. **Parked packages and modules** (`unwanted-things/dead-code-2026-11/`): `src/privacy`,
   `src/quality`, `src/contracts`; the unused agentic RAG loop in `account_intelligence/rag`
   (everything but `retrieval`, `tokenizer`, `models`); `verify/narrative.py` and the
   `narrative_reread.v1` and `source_reread.v1` prompts; their tests and the config files only
   they read (`drift.yaml`, `dsar.yaml`, `regression.yaml`, `rag.yaml`). The packages leave the
   hatch `packages`, coverage `source_pkgs` and import-linter `root_packages` lists.
2. **Orphan routes removed** (handlers and the helpers only they called parked in
   `unwanted-things/orphan-routes-2026-11/`): `POST /v1/accounts/{domain}/parity/edits`,
   `GET/PUT/DELETE /v1/chat/provider-key`, `POST /v1/chat/threads/{thread_id}/turns/stream`,
   `GET /v1/runs/pull-option`. The OpenAPI snapshot, the generated web client, the route
   catalogue and the isolation matrix were regenerated or updated. The saved-view routes stay
   (ADR-0051, owner confirmation pending).
3. **`config/` holds only files `src/` loads.** Registers no code reads move to
   `docs/registers/` (claims, messaging rules, pricing, secret rotation, SOC 2 controls, risk
   register, data-model spec, billing proposal, retention, minimisation map).
   `config_files.load_billing` is removed: it validated `billing.yaml` at startup but nothing read
   a value, and its proportional settlement rule did not describe the code.
   `tests/guardrails/test_every_config_file_is_loaded.py` enforces the rule; `README.md` is the
   one listed exception.
4. **Section 11 evidence stays honest** (as in ADR-0122): requirements that cited parked symbols
   now cite the register path and a not-yet-written symbol (`account_intelligence.<check>`), so
   they read Partial, not Done. `QS.regression_policy` now cites the real gate
   (`evalgate.gates:evaluate`, `.github/workflows/eval.yml`, `test_seeded_regressions_block`).
5. **Docs**: `docs/handover/` (written from the code, every statement referenced) replaces
   `docs/guide/` pages 1 to 6, parked in `unwanted-things/docs-guide-2026-10-29/`;
   `docs/frontend/` parked in `unwanted-things/docs-frontend-2026-11/`.
6. `unwanted-things/` is excluded from ruff: parked code is not product code, and its imports no
   longer resolve to first-party packages.
7. `pandas` moves from the runtime dependencies to the `dev` group: `src/` never imports it; one
   test reads a workbook with it. The `gpu` group keeps its pin (the worker image's GPU build).

## Consequences

* With the provider-key routes parked, no API writes a tenant's own Anthropic key; Ask still
  reads one if a row exists (`chat.credentials.encrypted_key_for_tenant`). Bringing BYO keys
  back means restoring the routes from `unwanted-things/orphan-routes-2026-11/` with a screen.
* Tables written only by parked code stay (migrations are append-only) and are marked in
  `config/ownership.yaml`.
* Inventory and grep evidence: `docs/audits/purge-2026-11.md`, `unwanted-things/README.md`.
