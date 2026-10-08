# ADR-0098: MuleSoft in the Salesforce lane (D-100)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The checklist counts MuleSoft under Salesforce and searches it on every account (S14); the skill description omits it (S13).

## Decision

MuleSoft is named in the Salesforce lane config of the agent layer (`config/skill/run.yaml`) and the Pass 2 prompt. The client-facing lane label in `config/lanes.yaml` is unchanged pending the owner (C30).

## Consequences

A label change alters the export rules hash and documents.
