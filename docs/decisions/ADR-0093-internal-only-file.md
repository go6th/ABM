# ADR-0093: The operator-only file is Internal Only (D-95, work item 10)

- Status: Proposed
- Date: 2026-10-08
- Deciders: TODO (product owner)

## Context

The skill's output spec creates an operator-only file named after a person (guide S11, S13). Platform rule 5: the internal view is called Internal Only and never named after a person. `config/output_spec.yaml` used `{account}_Operator_Only` and named the person in a comment.

## Decision

The file is `{account}_Internal_Only`; the comment is reworded. The person's name is held only as a sha256 word digest (`config/skill/name_digests.yaml`); `scripts/abm_name_scan.py` fails on any tracked file containing it and `agents.naming` rejects it in any tenant-facing output.

## Consequences

The export rules hash changed (docs regenerated). The workspace packer already refuses both name patterns in client packages.
