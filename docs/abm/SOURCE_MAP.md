# Source map: ABM Account Research skill v1.8

**Archive status: absent.** On 2026-10-08 the skill archive (`.skill`, 16 files) was not on this
machine; only the reader's guide was (`ABM_Skill_Readers_Guide_v2.docx`, repo root, 1,207 words).
The owner instructed a guide-only build. Everything below comes from the guide's Sources table,
which maps each statement to a file and line of the archive. **No rule text, checklist row, lesson,
phrasing list, sense-check question or consent text was written from memory**; those values are
`null` with `status: TODO` in config and the system runs at the conservative setting.

When the archive arrives: place it at `docs/sources/abm-skill/`, extract it into an empty
directory outside the repo (do not run its scripts or assets), fill in the "Archive text" column,
generate the lessons (`python scripts/abm_lessons.py generate .../lessons.md`), and reconcile the
checklist (C31).

## Files (guide S1, S11, S12)

| File | Size | Role | Encoded where | Archive text |
|---|---|---|---|---|
| `SKILL.md` | 11,402 bytes (S1) | Front door: eight steps (lines 23-32), split mode (34-36), rules that matter most (38-70), bundled files (88-96), updating (76-86) | `config/skill/run.yaml` | TODO |
| `references/checklist.md` | 23,326 bytes (S1) | Standing requirements, coverage rows, pre-delivery gate, change log 1.0 (line 172) to 1.8 (line 158) (S7) | rows: `config/checklist.yaml` (platform, unreconciled C31); versions: `config/skill_versions.yaml` | TODO |
| `references/lessons.md` | unknown | Corrections as general rules, dated 2026-09-20 to 2026-09-29 (S8) | `config/lessons.yaml` (empty) | TODO |
| `references/phase1-pull.md` | unknown | Phase 1 runbook: pulls everything, never interprets (line 7) (S9); setup step 0 (line 23) (S16); Pass 1 internal history (line 24) (S20); consent (line 29) (S19) | `src/agents/pull/`, `config/skill/consent.yaml` | TODO |
| `references/phase2-build.md` | unknown | Phase 2 runbook: mandatory sense check (lines 10-21) (S10); approved pulls (21-23) (S18) | `src/agents/sense_check/`, `config/skill/run.yaml` | TODO |
| `references/tools.md` | unknown | Tool routing, query patterns, costs; consent flow (line 66) (S11, S19) | `config/routing.yaml` (platform), `config/skill/pull_plan.yaml` (phrasings TODO) | TODO |
| `references/signals.md` | unknown | Signal tiers and lane questions (S11) | not encoded (TODO) | TODO |
| `references/output-spec.md` | unknown | Deliverable structure, grading scheme, operator-only file rules (line 66) (S11) | `config/output_spec.yaml` (platform; Internal Only) | TODO |
| `assets/` (Word helper, people and jobs workbook builders, tool-name stripper, executive deck example) | unknown | Build code and templates (S12) | platform exporters; the stripper is `agents.naming` plus `safety.client_output` | not executed |
| `scripts/` (dropped table rows, source links and banned dashes, third-party domain scan) | unknown | Automated checks (S1, S12) | platform quality gate and `scripts/scan_client_output.py` | not executed |

## Rules encoded (guide citations)

| Rule | Guide source | Archive location | Guard | Code |
|---|---|---|---|---|
| Eight steps of a run | S3 | SKILL.md 23-32 | G14-G17 | `agents/graph.py` |
| Split mode: pulls cheap, analysis strong | S4 | SKILL.md 34-36 | G11 | `config/model_routing.yaml` task kinds `abm_*` |
| Cover every source row visibly | S5 | SKILL.md 38-70 | G1 | `guards.coverage_verdict`, tracker reports per manifest row |
| A keyword match is a lead | S5 | SKILL.md 38-70 | G2 | `extraction._origin`, `guards.lead_check` |
| Three phrasings before dropping a lead | S5 | SKILL.md 38-70 | G3 | `guards.lead_drop_decision` (3 phrasings, 2 tool categories: C32) |
| Grade everything; a link with every fact | S5 | SKILL.md 38-70 | G4 | `guards.evidence_problem`, grading port |
| Never name the data tools in client files | S5 | SKILL.md 38-70 | G9 | `agents.naming`, build-node scan |
| Phase 1 never interprets | S9 | phase1-pull.md 7 | G5, G15 | `ManifestRow` schema; pull agent has no model |
| Phase 2 starts with a mandatory sense check (entity boundaries, adjacent organisations, hidden titles, contradictory facts) | S10 | phase2-build.md 10-21 | G6 | `sense_check/agent.py`, build precondition |
| Sense-check pulls run once approved | S18 | phase2-build.md 21-23 | G7, D-96 | `graph.sense_approval` |
| Ask before spending unapproved credits | S17 | checklist.md 33 (req. 14) | G7 | `guards.spend_route` (uses `spend.gate.decide`) |
| People-source consent before people pulls | S19 | tools.md 66; phase1-pull.md 29 | G8, D-97 | `guards.consent_decision` |
| Pass 1 searches mail, call notes, Drive | S20 | phase1-pull.md 24 | D-98 (off) | `guards.internal_history_allowed` |
| Operator-only file from internal sources | S11 | output-spec.md 66 | G10 | Internal Only view; `guards.renderable` |
| MuleSoft under the Salesforce lane, searched on every account | S14 | checklist.md 52 | D-100 | `config/skill/run.yaml` lanes |
| Updates repackaged in chat | S21 | SKILL.md 76-86 | D-101 (off) | repository only; `skill_versions.yaml` |
