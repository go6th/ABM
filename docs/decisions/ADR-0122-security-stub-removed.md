# ADR-0122: the no-op security, metrics, registers and unit-cost stubs are removed

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 1; the prompt names this ADR-0100, a number already taken,
  so it takes the next free number)

## Context

Four packages under `src/` returned constants and the tests that "covered" them asserted those
constants or `True`. The section 11 conformance report (`config/section11_spec.yaml`, evaluated by
`src/crosscut/conformance.py`) counted the symbols and the test names as proof, so it printed
Done for checks nothing performed, and `make scan` printed a pass for a stub.

| Stub | What it claimed | What it did |
|---|---|---|
| `security.pentest_gate.beta_gate()` | the client beta is gated on a pentest report | `return True` |
| `security.soc2.coverage()` | SOC 2 evidence coverage | `return 1.0` |
| `security.rotation.overdue(now)` | overdue secret rotations alert | `return []` |
| `security.secrets_scan.scan()` | no secret in code, logs or responses | `return []` |
| `metrics.report.generate()`, `metrics.linkcheck.check_all()` | success-metrics report, link check | `pass` |
| `registers.{risks,decisions,claims}.check()`, `registers.messaging.lint()` | removing a risk, decision or claim label fails the build | `pass` |
| `unitcost.rollup.*`, `unitcost.anomaly.check()` | broker, token, infra and review cost per account; anomaly alert | `pass` |
| `tests/crosscut/test_x5_security.py` .. `test_x8_registers.py` | the evidence above | `assert scan() == []`, `assert True` |

No product code imported any of them (only those four test files did).

## Decision

The four packages and four test files are parked in `unwanted-things/retired-stubs/` (owner rule:
move, never delete) and dropped from `pyproject.toml` (hatch `packages`, coverage `source_pkgs`,
import-linter `root_packages`). The spec no longer cites them, and a requirement with an empty
evidence list now evaluates **Missing** (before, an empty list read Done). What checks each claim
now:

| Requirement | Real check today | Report |
|---|---|---|
| `SC.secrets_manager` | CI job `secret-scan` runs gitleaks over the history with `.gitleaks.toml`; `make security-checks` runs it when gitleaks is installed | Done (file evidence `.gitleaks.toml`) |
| `SC.rotation_schedule` | **nothing**. `config/secrets_rotation.yaml` is a `status: proposal` table no code reads; no overdue alert exists | Partial (config only; `account_intelligence.secrets_rotation:overdue` is the check still to write) |
| `SC.soc2_pentest` | **nothing**. `config/soc2_controls.yaml` lists controls; no code measures coverage and no pentest report exists (`docs/security/PENTEST_SCOPE.md` is a scope, not a report) | Partial (`docs/security/pentest-report.md` missing) |
| `SC.untrusted_content` | `tests/agents/test_prompt_fences.py` (page text is fenced and cannot close its fence; canary prompts keep their injection guard, ADR-0120) | Done |
| `SC.prompt_tool_log` | the `tool_call_records.args_sha256` column; no review-store capture | Partial |
| `UC.broker_credits`, `UC.infrastructure`, `UC.review_minutes`, `UC.dashboard`, `UC.anomaly_alert` | **nothing** | Missing |
| `UC.model_tokens` | `config/pricing.yaml` (token prices exist); **no** per-account roll-up | Partial |
| `MX.report` | **nothing** | Missing |
| `MX.link_check` | the `link_checks.fetch_ref` column; **no** checker | Partial |
| `RR.risks`, `RR.decisions`, `CL.claims`, `CL.messaging` | the register files exist; no code fails the build when an entry is removed | Partial |

`make scan` is renamed `make security-checks` and runs real checks: gitleaks (when installed;
otherwise it says SKIPPED, never pass), `npm audit --omit=dev --audit-level=high` for the web,
`tests/phase4/test_ws10_gates.py` (client-output gates) and the `tests/isolation` suite. CI
already had real jobs (`dependency-audit` with pip-audit and npm audit, `secret-scan` with
gitleaks, Trivy on the images) and never ran the stub tests by name.

`tests/crosscut/test_no_stub_evidence.py` keeps it that way: the packages stay out of `src/` and
`pyproject.toml`, the spec cites no symbol in them, an empty evidence list is Missing, and
rotation, SOC 2, unit cost and the registers do not report Done.

## Consequences

`docs/crosscutting/CONFORMANCE.md` (regenerated 2026-11-03 from a migrated scratch database) drops
from "Done 50, Partial 1" to "Done 36, Missing 6, Partial 9". Rotation-overdue alerting, SOC 2
coverage, the pentest gate, unit-cost roll-ups and the register checks have no implementation;
each is open work, not a regression.
