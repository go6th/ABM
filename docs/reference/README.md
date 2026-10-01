# Reference set (T1)

What the platform must reproduce — the proven manual process and its artefacts.

| File | Contents |
|------|----------|
| [coverage_tracker.csv](coverage_tracker.csv) | The 33 coverage rows (5 Pass 1 + 28 Pass 2) with coverage question, proof required and grade rule per row. Tenant-configurable data at runtime, normative for the parity replay (T78). |
| [grading_scheme.md](grading_scheme.md) | The four client-facing grades (Confirmed, Probable, Hypothesis, Lead), their mapping to the numeric model, and the prospect-safe rule (I8). |
| [current_system.md](current_system.md) | How the manual Claude skill works today: folder layout and the 13-step run the platform automates. |

## External references (not in repo)

- **PRD** — *Account Intelligence: Business use case, product requirements and how the
  current system works* (6 Sep 2026) — business case, personas, standing rules.
- **Service design** — *Account Intelligence as a Service: Multi-client SaaS design,
  scheduled refresh and alerts, enhanced version* (6 Sep 2026) — tenancy, components,
  three-phase roadmap.
- **Architecture Spec v2.0** (in repo root as PDF) — technical source of truth.
- **Skill v1.5** — the live skill folder (SKILL.md, checklist, lessons, signals, tools
  playbook, output spec, gate scripts, builders) and nine delivered account file sets.

Exit criterion (T1): 33 coverage rows; four grades with definitions; skill layout and
13-step run documented. Verified by `scripts/check_reference.py` in CI.
