# ADR-0106: research caps and the token guard are on in every cloud profile

- Status: Accepted
- Date: 2026-11-03
- Deciders: owner (fix prompt A)

## Context

Staging deployed `RESEARCH_CAPS=off` from 2026-10-21 (owner decision, "for the time being"). With
the switch off `dev_limits.research_uncapped()` is true and the run dollar cap
(`connectors/spend_guard.check_cap`), the repeat-call limit (`check_repeat`), the 3x token guard
(`check_tokens`, unless `TOKEN_GUARD=on`), the per-agent budgets (`agents/contracts.py`), the
slice ceiling (`app/container._slices`, `max_slices: 16` from `config/workflow_v1.yaml`), the
credit pool (`replica/credits.py`), the researches per user (`replica/limits.py`) and the plan
limits (`metering/limits.py`) are all lifted. Failed uncapped runs cost the owner real money.

## Decision

`infra/stack` declares `research_caps` and `token_guard`, both defaulting to `on`, and passes them
to every service and job as `RESEARCH_CAPS` and `TOKEN_GUARD`. `infra/envs/staging` now defaults
both to `on`; `infra/envs/prod` inherits the stack defaults. `test_caps_default_on_in_cloud_profiles`
reads the `.tf` files as text and fails if either environment would deploy anything but `on`.
A staging or production process that starts with the caps off logs `research.caps_off` (WARNING,
field `caps_off` lists every lifted cap: `dev_limits.warn_if_uncapped`, called from
`app.container.build_container`). Both switches are registered in the typed environment registry.

## Consequences

Switching the caps off again needs an explicit tfvars override and shows in every startup log.
The staging plan made for this change also carries unrelated drift (Cloud SQL
`activation_policy NEVER -> ALWAYS`, env vars set by hand with gcloud, IAM and alert changes): it
must be reviewed as a whole, or the two env vars applied with a targeted
`gcloud run services update --update-env-vars RESEARCH_CAPS=on,TOKEN_GUARD=on`, before any apply.
