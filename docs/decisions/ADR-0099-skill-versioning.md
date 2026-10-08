# ADR-0099: Skill versioning through the repository (D-101)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The skill is version 1.8 with a change log 1.0 (2026-09-20) to 1.8 (2026-09-29) (S7); updates were repackaged in chat (S21).

## Decision

`config/skill_versions.yaml` records the version; every run pins the skill config version (hash of `config/skill/`, `agents.yaml`, `lessons.yaml`, `skill_versions.yaml`). Updates ship by pull request with the eval gate; `in_app_repackaging: true` is refused at load.

## Consequences

Intermediate versions 1.1 to 1.7 are TODO until the archive is read.
