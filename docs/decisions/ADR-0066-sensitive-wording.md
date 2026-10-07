# ADR-0066: Sensitive findings are preparation only (D-76)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

The skill: breach, layoff and lawsuit signals are preparation, never an outreach hook.

## Decision

* Detection by event category in code (`alerts.sensitive.CATEGORY_OF`).
* Rendering only from fixed templates, labelled "Preparation only, not an opening line.", in every
  channel; digest-only; a neutral subject ("Account context update").
* `alerts.lint` rejects outreach phrasing in every rendered alert, digest, Summary diff and CRM
  payload; CRM payloads refuse sensitive findings outright.

## Consequences

Proven by `test_ws5_wording.py` and `test_ws5_alerting.py`.
