# ADR-0097: Lessons as code (D-99)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

lessons.md records corrections as general rules (S8). They must be enforced, not remembered.

## Decision

Each lesson becomes an entry in `config/lessons.yaml` (id, dated source line, rule, enforcement code|prompt|process, guard, test). `scripts/abm_lessons.py check` fails on a lesson without a guard or test and reports INCOMPLETE while the archive is absent; `generate` creates entries and stub tests from the archive. New lessons arrive by pull request with a test.

## Consequences

No lesson is enforced yet (archive absent). The lessons.md format is assumed ([VERIFY]).
