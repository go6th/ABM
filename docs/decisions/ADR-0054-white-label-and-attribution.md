# ADR-0054: White label keeps the 6th agents attribution (D-64)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

Tenants want their own look; rule 6 attributes output to "6th agents".

## Decision

* Branding (`branding_assets`, `config/views.yaml` `branding`) changes the display name, logo
  (PNG or JPEG only, size and pixel limits, re-encoded; SVG refused) and accent colour (contrast
  checked) in the app; the Word and PDF header carries the display name.
* The attribution "6th agents" is fixed; no setting removes it. The sub-processor text is unchanged.
  Custom domains are a follow-up.

## Consequences

Partial: the Word header carries the display name but not yet the logo and accent colour
(follow-up). Proven by `tests/phase3/test_ws4_render_safe.py` and the branding tests.
