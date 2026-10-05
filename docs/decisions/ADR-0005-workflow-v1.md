# ADR-0005: Workflow v1 stages and ordering

Status: Accepted for workflow scaffolding; product mapping remains proposed where noted.

## Decision

Represent the 13 PRD steps as a validated, YAML-backed DAG. Version 1 executes its
topological order sequentially. `distill_grade` contains the PRD step 10 grading work;
the separate Architecture judge `verify` operation is an internal substage of that
group and must use a judge tier distinct from the generator. It does not move the
three-query PRD step 9 lead-verification loop, which remains the preceding stage.
The declared dependency graph is sufficient to enable later parallel scheduling
without changing stage identity or state references.

## Evidence and limits

The checked-in PRD reference documents the thirteen research steps and the
Architecture reference documents the later judge node. The original PRD Appendix A,
which the request says contains row-to-stage labels, is not available in this
checkout. The checked-in checklist provides row keys, labels and pass number only.
Consequently, workflow coverage ownership is a proposed mapping from those fields.
The user confirmed that checklist row P2.15 belongs to `pass1_relationship` on
2026-10-02; other proposal annotations remain unresolved pending Appendix A.

## Consequences

Missing repositories, providers, builder, gate, wallet settlement and model adapters
are ports. Their absence must produce `NotConfigured` and a partial result, never an
empty successful stage. The current wallet schema reserves credits, while the run
budget is USD; no conversion or settlement is inferred here. Decision D-04 and a
production wallet adapter are required before settlement can safely be enabled. Model
profile routing for a distinct judge is also a TODO; the current model config routes
verification and synthesis to the same profile in its active arm.
