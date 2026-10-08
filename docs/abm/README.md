# ABM agent layer

The ABM Account Research skill (v1.8) as production agents inside the research platform. Built
**guide-only** on 2026-10-08: the skill archive was absent and the owner instructed a build from the
reader's guide; every archive-only value is `status: TODO` (see `SOURCE_MAP.md`).

| Document | What it holds |
|---|---|
| `SOURCE_MAP.md` | The archive's files, the rules encoded and where, with guide citations |
| `AGENTS.md` | Agent catalogue, what code decides regardless of the model, the run graph |
| `CONVERSIONS.md` | Operator-in-the-loop behaviours turned into settings, approvals or switched-off features |
| `LESSONS.md` | Lessons as code and the (empty, pending archive) lessons table |
| `RECONCILIATION.md` | Differences between the skill and the platform config, and how each was resolved |

Code: `src/agents/`. Config: `config/skill/`, `config/agents.yaml`, `config/lessons.yaml`,
`config/skill_versions.yaml`. Tests: `tests/agents/`, `tests/phase3/test_abm_modes_api.py`.
Decisions: D-95 to D-102 (ADR-0091 to ADR-0099); conflicts C30 to C34; open items OI-ABM-1 to 7.

Commands:

```
python scripts/abm_lessons.py check            # 0 ok, 1 failed, 3 incomplete (archive absent)
python scripts/abm_name_scan.py                # fails on the forbidden person name (digest)
pytest tests/agents tests/phase3/test_abm_modes_api.py
```
