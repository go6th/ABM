# SaaS conversions (guide S15 to S21)

Source of truth: `config/skill/conversions.yaml`. Each conversion names its enforcer and its test
(`tests/agents/test_conversions.py`); a test checks both exist.

| Skill behaviour (operator in the loop) | Source | SaaS form | Decision | Test |
|---|---|---|---|---|
| Triggers on the operator naming a target account | S13 | A typed run request only (form, API `mode`, scheduler); no free-text trigger | D-102 | `test_conversion_trigger_is_a_typed_request` |
| Setup asks for the pursuing client and confirms the credit cap | S16 | Pursuing client is a tenant setting; the cap is approved in the app before any paid call (`cap_approval` interrupt) | D-96 | `test_conversion_cap_confirmation_is_setting_plus_approval` |
| Ask before spending unapproved credits | S17 | Answered by the credits confirmed before the run (REPLICA_PROMPT): reaching them marks the remaining paid rows Blocked `budget_cap` and the run ends BUDGET_EXCEEDED with what it found; it never pauses to ask | D-96, FIX_LOG D7 | `test_conversion_ask_before_spend_is_the_confirmed_credits` |
| Sense-check pulls run once approved | S18 | Typed, priced proposals; run within the approved cap or after approval; otherwise rows Partial `pull_not_approved` | D-96 | `test_conversion_sense_check_pulls_need_approval` |
| People-source consent flow before people pulls | S19 | Tenant-level "People data consent" record (who, when, text version, scope); none valid until the consent text is approved | D-97 | `test_conversion_consent_flow_is_a_tenant_record` |
| Pass 1 searches mail, call notes, named Drive folders | S20 | Off for every tenant; on only with admin consent, named folders and a connected Phase 3 connector; content private | D-98 | `test_conversion_internal_history_off_by_default` |
| Output spec creates an operator-only file from internal sources | S11 | The Internal Only view and file (`{account}_Internal_Only`); never named after a person | D-95 | `test_conversion_operator_only_file_is_internal_only` |
| Checklist updates repackaged in chat | S21 | Repository and pull request with the eval gate; no in-app repackaging | D-101 | `test_conversion_updates_go_through_the_repository` |
