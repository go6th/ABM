# Role matrix (D-61, generated from `config/roles.yaml`)

Status: proposal (ADR-0051). Y = allowed; `step-up` = needs a multi-factor sign-in within
`config/session.yaml` `step_up_window_s`; `own` = only on the caller's own objects. Staff roles
(`operator`, `support`) are never tenant roles and appear in no row. No group mapping means no
role and no access.

| Action | admin | analyst | seller | viewer | Notes |
|---|---|---|---|---|---|
| `read_public` | Y | Y | Y | Y |  |
| `read_internal` | Y | Y | Y |  |  |
| `export_files` | Y | Y | Y | Y |  |
| `add_account` | Y | Y | Y |  |  |
| `start_run` | Y | Y | Y |  |  |
| `cancel_run` | Y | Y | Y |  |  |
| `approve_run` | Y | Y | Y |  |  |
| `configure_alerts` | Y | Y | Y |  |  |
| `save_own_view` | Y | Y | Y |  | own |
| `edit_shared_views` | Y | Y |  |  |  |
| `edit_tracker_notes` | Y | Y |  |  |  |
| `give_feedback` | Y | Y | Y | Y |  |
| `revoke_own_session` | Y | Y | Y | Y | own |
| `approve_cap` | Y |  |  |  | step-up |
| `manage_users` | Y |  |  |  | step-up |
| `manage_sso` | Y |  |  |  | step-up |
| `manage_settings` | Y |  |  |  |  |
| `manage_connectors` | Y |  |  |  | step-up |
| `read_audit` | Y |  |  |  |  |
| `export_audit` | Y |  |  |  | step-up |
| `read_usage` | Y |  |  |  |  |
| `grant_support` | Y |  |  |  | step-up |
| `request_deletion` | Y |  |  |  | step-up |

Invariants proven by `tests/phase3/test_ws2_roles.py`: the table equals a hand-written oracle;
analyst = seller plus `edit_shared_views` and `edit_tracker_notes`; the last active admin cannot be
removed or demoted; nobody changes their own role; operator and support names are refused as
tenant roles. Every route names one action (`api.policy.tenant_route`); `scripts/check_route_catalog.py`
refuses a route without one.
