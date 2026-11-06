# ADR-0129: replay keys are versioned, pinned by a golden file, and a resume warns when they changed

- Status: Accepted (code)
- Date: 2026-11-03
- Deciders: owner (fix prompt E, task 2)

## Context

A resumed run reuses what its source run paid for only when the keys match: a provider call by
its ledger `CallKey.args_hash` (`connectors.ledger.args_hash` over provider, operation and the
arguments, which carry the `config/skill/pull_plan.yaml` search template and the canonical URL),
a model reply by `llm.reply_cache.reply_key` (task kind, tier with the effort suffix, token
limit, system prompt, user text with the run id normalised out; `RunDeps.resolve_model` also
keys the in-run replay digest on the same request). Any change to a prompt, a template or a
canonicalisation rule silently changes keys, and every run started before it re-pays those calls
on resume.

## Decision

- **`REPLAY_KEY_VERSION`** lives in one place: `config/research_cache.yaml`
  `replay_key_version` (read by `connectors.cache.replay_key_version()`).
  - `1`: every run before 2026-11-03 (a run with no recorded version counts as 1);
  - `2`: after fix prompt C, which changed the reply keys of `second_pass_posting`,
    `abm_pass1_extract`, `abm_pass2_extract` and `abm_people_extract` (prompt text and payload
    fences) and added the effort suffix on the extraction kinds. Provider call keys did not change.
- **Stored on each run**: `runs.lifecycle.create_run` writes `run_input.replay_key_version`
  (server-set, outside the idempotency hash). No migration.
- **Golden keys**: `tests/golden/replay_keys.json` holds every `CallKey` (provider:operation:hash,
  175) and every reply key (task kind:tier:hash, 51) of one fixed hermetic company research
  (`LLM_FAKE`-style hermetic model and `CONNECTORS=hermetic` providers, the offline stack of
  `tests/system/test_offline_end_to_end.py`). `tests/system/test_replay_keys.py::test_replay_keys_stable`
  regenerates them and fails with "replay keys changed: resumed runs started before this commit
  will re-pay these calls; bump REPLAY_KEY_VERSION and record it in the ADR".
  `REPLAY_KEYS_REGEN=1` rewrites the file (only together with a version bump recorded here). The
  test's own tenant, account and run ids are replaced by placeholders before hashing (production
  resumes are always the same tenant and account; the run id is normalised by `reply_key`
  already). Verified stable over three consecutive runs.
- **Resume warning**: the account card's `StoppedRun` gains `replay_extra_usd` (API contract,
  OpenAPI and web client regenerated). When the stopped run's version is older than the current
  one it is the upper bound of what the resume may pay again; `ResumeResearch` shows "This resume
  may cost up to $X more: ..." above the Resume button, before the user confirms.
  - **Estimate**: everything the source run paid for, its priced model replies (the larger of
    `llm_calls` and `model_reply_log` served by the model, as the token meter counts) plus its
    provider spend (`account_runs.spent_usd`), rounded up to the cent. A run does not record
    which of its calls used the keys a version changed, so the bound assumes none is reused;
    unchanged keys are still reused at no cost, so the real cost is usually lower (for 1 -> 2 only
    the extraction replies changed, no provider call).

## Findings and limits

- `abm_distill` and `abm_sense_check` requests list the run's facts by database id and in id
  order: their keys differ on every run, so those two replies are never reused across runs
  (in production either) and are left out of the golden file. Ordering the facts by a stable key
  and replacing ids by stable codes would make them reusable; not changed here.
- Code disagreement with the prompt: there is no resume estimate dialog. Resume is one button
  (`ResumeButton`, `POST /v1/research/resume`, no Proceed/Cancel step); the warning is shown on the
  account page's stopped-run card where the button sits. The Research board and run page reuse
  `ResumeButton` without the card and do not show it.
