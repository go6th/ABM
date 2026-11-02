# Skill 1.12 package (draft, not installed)

Version 1.12 of the `abm-account-research` skill, written on 2026-11-02 from three screens in which a
cheaper model answered workspace questions from retrieval snippets (40 of 1,484 people; 14 Salesforce
mentions against 438 in 7,148 postings) while a model that ran SQL over every file did not. See the
2026-11-02 entry in `references/lessons.md`.

## What is here

Only what changes from v1.11 (every other file of the package is unchanged):

| File | Change |
|---|---|
| `abm-account-research/assets/build_sqlite.py` | new: builds `{Account}_phase1.sqlite` from a phase 1 package and runs logged read only queries |
| `abm-account-research/references/workspace-schema.md` | new: the tables, summary tables and example queries |
| `abm-account-research/references/phase1-pull.md` | step 12 builds the database first, three gate rows, the zip carries the database (full file) |
| `abm-account-research/references/phase2-build.md` | phase 2 works through the database and cites query ids (full file) |
| `patches/SKILL.md.patch` | new section "Answering questions about a workspace"; the count rule cites its query; bundled files and reading list |
| `patches/references_checklist.md.patch` | version 1.12, requirement 20 and new 27, gate line 12, change log |
| `patches/references_output-spec.md.patch` | Summary section 13 Query log, the no-hedge rule for counted figures |
| `patches/references_lessons.md.patch` | the 2026-11-02 entry |

The four files that name the operator are shipped as zero-context patches, not copies, so no
personal name is duplicated in the repository (the name scan acknowledges those names only in the
installed package by its digest). Apply them to a copy of the v1.11 package, from its root:

```bash
patch -p1 < patches/SKILL.md.patch
patch -p1 < patches/references_checklist.md.patch
patch -p1 < patches/references_output-spec.md.patch
patch -p1 < patches/references_lessons.md.patch
```

(the patches are named with `_` for the folder; each one's header names the file it patches).

## Why it is not installed

`config/skill/source.yaml` records the skill as the operator-supplied v1.11 package "extracted
unchanged", and `tests/agents/test_skill_context.py::test_v1_12_package_not_available` (strict xfail)
keeps v1.11 from being relabelled. To ship 1.12: copy these files over a copy of the v1.11 package,
repackage with the skill-creator packaging script, and install it here. Installing means:
`config/skill/archive/` files replaced, `source.yaml` skill version and `package` name set to 1.12,
`file_count` 17 to 19, the xfail test removed, and the lesson mapped to a guard (`tests/agents/test_lessons.py`
turns every dated lesson into a check). Tested with `tests/agents/test_build_sqlite.py`.
