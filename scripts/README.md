# `scripts/`: generators and gates

Nothing here runs in production. Each script is either a **gate** (exits non-zero when a rule is
broken; run by `make` and CI) or a **generator** (writes a document or file that is committed
and checked for drift). Run them with `uv run --frozen python scripts/<name>.py`.

## Gates (run by `make static`, `make contract`, `make obs-verify`, CI `quality` job)

| Script | Checks | Run by |
| --- | --- | --- |
| `check_comments.py` | every Python, SQL and YAML file explains itself in its header | `make static` |
| `check_guarantees.py` | every `GUARANTEE(<test>)` tag names a test that exists | `make static` |
| `gen_loops.py --check` | every bounded loop carries a `LOOP(name): cap=…; exit=…` comment; `docs/architecture/loops.md` is current | `make static` |
| `vulture_whitelist.py` | the whitelist vulture reads (names that look unused but are an interface) | `make static` |
| `check_route_catalog.py` | every API route has exactly one policy and an audit class | `make contract` |
| `check_routing_governance.py` | a change to `config/model_routing.yaml` has a recorded decision in `docs/routing/DECISIONS.md` | CI, eval gate |
| `check_telemetry.py` | every metric and span in code is in the telemetry contract | `make obs-verify` |
| `check_runbooks.py` | every alert names a runbook that exists with the required sections | `make obs-verify` |
| `check_conformance.py` | roadmap section 11 conformance report (`docs/crosscutting/CONFORMANCE.md`) | manual, CI |
| `abm_name_scan.py` | no guarded name (held only as a digest) appears in any tracked file | tests, manual |
| `abm_lessons.py check` | every lesson in the skill archive is encoded in `config/lessons.yaml` with a test | tests |
| `canary_scan.py` | no log canary value reached a sink | security tests |
| `check_rls_catalog.sql` | no tenant table without forced RLS (run by `make check` against a scratch database) | `make check` |
| `preflight_deploy.py` | deployment inputs and Secret Manager wiring are present, without printing values | before a release |
| `check.sh` | the full pre-PR gate against a scratch database | `make check` |

## Generators (the output is committed; CI fails when it drifts)

| Script | Writes |
| --- | --- |
| `export_openapi.py` | `docs/api/openapi.json` (the API contract) |
| `gen_web_client.py` | `web/lib/gen/api.gen.ts` and `labels.gen.ts` (the typed client the screens use) |
| `wiring_audit.py` | `docs/WIRING.md` (every screen call site matched to an API operation) |
| `gen_env_docs.py` | `docs/ENVIRONMENT.md` from the typed environment registry |
| `gen_observability.py` | `infra/modules/observability/generated_alerts.tf.json` and `generated_dashboards.tf.json` from `config/telemetry/` |
| `gen_isolation_matrix.py` | the tenant-isolation coverage matrix (`tests/isolation/matrix/`) |
| `gen_loops.py` | `docs/architecture/loops.md` |
| `module_map.py` | `docs/architecture/module-map.md` (who imports whom) |
| `gen_stage_map.py`, `gen_tracker_docs.py`, `gen_jobs_docs.py` | `docs/workflow/stage-map.md`, `docs/tracker/transitions.md`, `docs/jobs/*.md` from config |
| `gen_ownership_migration.py` | migration 0063 from `config/ownership.yaml` (one-off, kept for the record) |
| `simulate_parallel.py` | `docs/parallel/simulation-report.md` |

## Local development helpers

| Script | What it does | Run by |
| --- | --- | --- |
| `doctor.py` | reports toolchain and stack readiness without printing any value | `make doctor` |
| `verify_wiring.py` | the hermetic end-to-end wiring matrix against `make up` | `make verify-wiring` |
| `seed_pilot.py` | two SYNTHETIC pilot tenants for demos | `make seed` |
| `seed_replica_demo.py` | the demo workspace that matches the design prototype (dev only; needs `DEMO_PASSWORD`) | manual |
| `smoke_image.sh` | the built image against the local database: non-root, `/readyz` 200, clean SIGTERM | `make smoke` |
| `run_isolation.sh` | the isolation suite against a given database | manual |
| `deploy_staging.sh` | PRINTS the staging release commands (runs nothing) | `make deploy-staging` |
| `scale_smoke.py` | synthetic scale run; writes `docs/phase3/scale_smoke.md` | manual |
| `token_savings.py` | what the research cache saved in provider calls and model tokens | manual |
| `benchmark_model_split.py` | summarises supplied benchmark results (makes no model calls) | manual |
