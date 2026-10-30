# File build: one content check must never end a finished research (2026-10-31)

Branch `feat/final-product`, working tree on top of 073e3104 (uncommitted, not deployed). Hermetic work only: no live run, no provider, no deploy.

## The failure

Staging, worker rel-2a00f96: a full research read, graded and wrote the narrative in 20 minutes, then the FILE BUILD refused and the run ended PARTIAL with no files. The log line was `exports.build_failed error_code "leak,tool_name" reason "leak=2,leak=5,tool_name=1"`. The run before it failed the same way with `aggregator_not_lead=16` (fixed 2026-10-30: the row is withheld). The pattern: ONE content-level check fails the whole research after about 20 minutes of paid work.

What that log line meant (`exports.builder._failed`, `_verify`):

* `leak=2` and `leak=5`: two produced files failed the final byte scan (`scan_package`: the denylist plus the run's restricted terms) with 2 and 5 hits (one hit is a member of the package and a pattern; a Word file holds the same link in its relationships and its text).
* `tool_name=1`: one produced file failed the third-party scan. `tool_name` was the FIXED label of three different rules there (a data tool's name, a link to a data tool's host, and the word the providers use for their own unit), so the line cannot say which.

The staging database is private-IP only, so the matched text was not read and the exact cause of that run is NOT established. What is established is every way the code let a content check end the research: a restricted term, the cost word, a tool host and a tool name were injected into every string of a synthetic snapshot one at a time (the scripts are in the session scratchpad; the checks that came out of it are the tests below). Each of the following ended the research before; none does now. The first three are the likely causes of `leak=2`, `leak=5`, `tool_name=1`:

1. The researched account's own name or domain was "another account": the SQL listed every account of the tenant (a removed one, the same company in another user's workspace, the same domain), so the name was a forbidden term that is in the title, header and properties of every file. All five files failed. (Deleted accounts: no longer counted. Same domain or same name: no longer counted. A smaller name that is a whole word of this account's name: no longer counted.)
2. A source, person, posting, detection or finding LINK that names another account (a page on its site: `www.<other domain>/...`). Links reached the files unchecked and showed up twice in a Word file (relationship and text): `leak=2`, `leak=5` is the size such a link makes. They are now judged like the data tool links already were: the rows that rest on them are withheld and listed (`restricted_link`). Caveat: a URL slug that contains a common-word account name ("Apple", "Delta") is blocked too, because the final scan matches it too.
3. The cost word or a data tool's host or name inside a job description, a news headline, a quote or a source link (for example "R&D tax credits"): `tool_name=1`. They are withheld where they are printed.
4. The run's own records in the zip (pull log, searches, detections, entity lock with its look-alike companies, the manifest's registry sentences) carried restricted terms or the cost word. They are scrubbed.
5. A text path the mapping did not check (the Platform teams dates were one; found by the injection). The last line of defence replaces it and renders again.
6. The agent layer's own guard (G9, applied to every stored file after the build, FAILING the run) refused texts the builder passed: a provider's name that is also a word ("Parallel Computing Engineer"), the guarded person's name. The builder now applies the same rule to every text it prints.

## Every way the file build can end a research

"Before" is the tree at 073e3104 (`build_all` and what the run does with its result). "After" is this working tree. FAIL = the research ends PARTIAL `build_failed` with no files; ROW / BLOCK / CELL / FILE = left out or replaced and listed in the manifest (counts and rule names, never text), the rest delivered.

### Mapping (src/exports/readers, `MapContext`)

| Rule (code) | Where | What it protects | Before | After |
| --- | --- | --- | --- | --- |
| `non_public_row` | summary lanes and sections | a private row is never in a client file (B8) | FAIL | FAIL: security tripwire; the reader already selects public rows only |
| `grade_vocabulary` | `policy.fact_violations` | grade outside the vocabulary | FAIL | FAIL: vocabulary defect in code |
| `confirmed_without_primary_read`, `statement_flag_disagrees`, `question_missing`, `internal_in_public_file` | `fact_violations` | statement policy: nothing is stated that the grade does not allow (B7) | FAIL | ROW (the finding) withheld |
| `uncited_graded_row`, `missing_source_code`, `missing_url`, `bad_url`, `tool_link` | lane facts | a finding is printed only with a registered, usable source | FAIL (tool_link already ROW) | ROW; the rule listed is the first one that refused a source |
| `unknown_source_code` | people, postings, detections, findings, lane facts | a stored code the registry does not know (corrupt provenance) | FAIL | ROW, listed (never printed) |
| `unknown_source_code` | section codes | same, for an authored block | FAIL | BLOCK |
| `narrative_without_code`, `section_text_length` | `_sections` | every statement is cited; length cap | FAIL | BLOCK |
| `text_missing`, `name_missing` | every required text | nothing is printed in place of a blank | FAIL | ROW / BLOCK |
| `section_kind_vocabulary`, `origin_vocabulary`, `posting_status_vocabulary`, `posting_class_vocabulary`, `source_kind_vocabulary` | sections, people, jobs, evidence | closed vocabularies | FAIL | FAIL: vocabulary defect in code |
| `row_cap` | each file | no silent truncation (B3) | FAIL | FILE: that file is left out whole, the others delivered |
| `missing_narrative` | Summary | a Summary has a narrative | FAIL | FILE: the Summary is left out (it also happens when every narrative block was withheld by a rule) |
| `missing_tracker` | Summary | the coverage tables | FAIL | FILE: the Summary |
| rows read + withheld differ from the stored count | people, jobs, tech | row conservation (B3) | FAIL (`missing_content`) | FAIL: structural |
| `orphan_citation` (cited code absent from the register) | `builder.map_snapshot` | the citation graph | FAIL | FAIL: structural (a mapper bug) |
| number registered twice with another value | `exports.numbers` | one count, one value (B6) | uncaught exception | FAIL, typed (`banned_content`) |
| `aggregator_not_lead`, `lane_unknown`, `unregistered_number`, `banned_dash`, `stale_phrase`, `abbreviation_first_use`, `partner_framing`, `unconfirmed_stated`, `section_fact_not_rendered`, `question_missing` (section), `speaker_missing`, `leak` (section), `entity_rule_*` | summary / jobs | content rules | BLOCK or ROW (done 2026-10-21 to 2026-10-30) | unchanged |
| a restricted term, tool name, cost word, tool host, provider name, guarded person name inside a printed text | `MapContext.rendered`, `quote` | B9, spec 12, G9 | CELL replaced for the denylist and restricted terms only | CELL replaced for all; counted `part:rule=n` (`leak`, `tool_name`, `internal_id`, `cost_word`, `data_tool_host`, `provider_name`, `guarded_name`, `third_party_domain`) |
| a link that names another account, an excluded company or a listed-out person | `MapContext.link_rule` (new) | B9 | reached the file unchecked; the final scan FAILED the build | ROW withheld (`restricted_link`); a second profile link is left off, the person stays |
| a link with the cost word | `link_rule` | spec 12 | FAIL | ROW (`cost_word_link`) |
| a link to a data tool, its host, or a provider name | `link_rule` | spec 12, G9 | ROW (denylist only); host and provider names FAILED | ROW (`tool_link`) |

### Verification of each produced file (`ExportBuilder._verify`)

| Check | What it protects | Before | After |
| --- | --- | --- | --- |
| `scan_package` (denylist and restricted terms over every member) | B9 | FAIL (`leak`) | the file's refused strings are replaced once and the file rendered again (`late_replaced`); if still refused, or the text is an address or the file's own wording: FILE (`kind:leak`) |
| third-party scan (tool name, tool host, the cost word) | spec 12 | FAIL, always labelled `tool_name` | same as above; the rules are named `tool_name`, `cost_word`, `data_tool_host` |
| `doc_checks`: `banned_dash`, `stale_phrase`, `excluded_name`, `orphan_citation` (text) | D-24 and the citation rule on the produced text | FAIL | stale phrase, excluded name, unknown citation: sweep and render again; dash: FILE |
| keys re-read from the file differ from the rows selected (row conservation) | B3 | FAIL | FAIL: structural |
| PDF row presence, `docx_rowcheck` found a lost row | B3 | FAIL | FAIL: structural |
| PDF row presence / `docx_rowcheck` converter unavailable, timed out or could not run | infrastructure | `docx_rowcheck` FAIL ("fails closed"); row presence `not_run` | `not_run` with its reason in the manifest and the log, both checks |
| cached formula values, workbook recalculation | B12 | FAIL | FAIL: structural |
| renderer failure (timeout, output too large, malformed, exit status) | infrastructure | FAIL | FILE (`kind:render_failed`) |
| a render model fails its own validation | code defect | uncaught exception (the run's retries repeat it) | FAIL, typed (`model_validation`, field and error type, never the value) |
| any other exception inside the build | code defect | uncaught exception | FAIL, typed (`internal_error`, the class, never the message) |
| no Word renderer configured | configuration | `not_configured` | unchanged |

### The workspace zip

| Check | Before | After |
| --- | --- | --- |
| member names, sizes, caps, hashes (`check_zip`, `UnsafePackage`) | FAIL | caps: built again without the archived copies (`workspace.releases:all:size_cap`); the rest FAIL |
| restricted term, tool name, tool host, cost word in a run record, the render data, the manifest | FAIL | the member stays, scrubbed (marker `[withheld]`, `members_scrubbed` counts them) |
| the same after scrubbing | FAIL | the zip alone is left out (`workspace:<rule>`), the five files delivered |
| a set with a file left out | not possible | zip holds the others and their data; RESTORE.md lists only those |
| an archived copy a rule refuses | left out and listed (2026-10-25) | unchanged; its member name now follows the cleaned title |

### Reading the stored run (`PgContentReader`)

| Check | Before | After |
| --- | --- | --- |
| no such run, settings no longer validate, people record without counts | FAIL | FAIL: structural |
| keyset row count differs from the snapshot count | FAIL | FAIL: structural |
| a stored posting text fails its hash | FAIL | FAIL: structural (corrupt payload) |
| `people.export` scanner hit on an excluded person's NAME | FAIL (`LeakError`) | the name is not listed, the person stays counted |
| other accounts' names and domains | included removed accounts and the same company | removed accounts, the same domain and the same name are left out (`pg._OTHER_ACCOUNTS`, `RunInputAdapter.other_account_names`); the account's own identity is subtracted again at the mapping (`own_names`) |

### After the build (outside `build_all`)

| Step | Effect | Before | After |
| --- | --- | --- | --- |
| `agents.graph.build`: `assert_client_clean` (G9) on every stored file | the run is FAILED, not PARTIAL | refused texts the builder passed | the builder applies the same rule (`agents.naming.client_rule`, registered into `exports.policy`) to every text, so a built file passes; the step itself is unchanged (tripwire; see the owner decisions) |
| `documents.gate.PreDeliveryGate`: `tracker_rows`, `fact_citations`, `internal_label`, coverage verdict | run-level: PARTIAL `gate_failed`, nothing delivered | unchanged | unchanged |
| gate `deliverable_set` | run-level: a missing file | a file the builder left out counted as missing | not missing: reported by name as `client_output` (file-level) |
| gate `client_dashes`, `client_terms`, `third_party_domains`, `rows_dropped`, `leak_check` | file-level: PARTIAL `files_withheld` | unchanged | unchanged; new file-level `client_output` for a file the builder left out |
| `POST /v1/exports` package: client-output refusal, parity | the package is blocked | a left out file made the package blocked (`parity_mismatch`) | the package carries the files the set holds |
| the file listing | newest version of each kind, minus gate-held kinds | an older file stood in for a left out one | a kind the account's latest build left out is not listed, and no older version stands in |

## What each fix does

1. **Own identity is never "another account"** (`policy.names_own`, `restricted_terms`, `readers.common.own_names`, `restricted_of`). The account's name, legal name, subsidiaries, domains, careers host and brand, and every whole word of them, are dropped from the restricted set. SQL: the other accounts exclude `deleted_at IS NOT NULL`, the same domain and the same name. The same SQL is `RunInputAdapter.other_account_names`.
2. **One funnel for every printed text** (`MapContext.block_rule`): the denylist, the run's restricted terms and guarded names, provider codes and hashes, the cost word, a data tool's host, and the agent layer's guard. `rendered` and `quote` replace a refused text by the withheld note and count it by file part and rule; the Summary withholds a block under the rule's name.
3. **One funnel for every link** (`MapContext.link_rule`): decoded and as written; sources get a rule (`denied_rules`), every row that rests on one is withheld and listed, and a blocked code is dropped from a quote's and a hypothesis's citations (that case ended builds with an orphan citation).
4. **The account's own name** may itself be refused (an account called after a data tool, with the cost word, or `Parallel ...`): the printed name has the refused words replaced by `[withheld]`, authored text that names the account says the printed name, and a name that is still refused becomes `Account <hash>`.
5. **Last line of defence** (`exports/sweep.py`, `ExportBuilder._produce`): when the final scan of a produced file still refuses it, every string of the model that a rule refuses is replaced, the file is rendered once more, and a file that is still refused is left out (`FileHeld`). The set is delivered with the other files, the zip lists only those, the stored manifest of every row carries `files_withheld`, the gate reports the file by name (`client_output`, file-level) and the run ends PARTIAL `files_withheld`. If nothing is left to deliver, the build fails and names every file and rule.
6. **The run's records** (pull log, searches, detections, entity lock, the render data and the manifest the zip embeds) are scrubbed instead of failing the zip; a zip that still fails is left out alone.
7. **Diagnostics.** The manifest carries `texts_withheld` (`part:rule=n`), `files_withheld` (`kind:rule`), `late_replaced` (`kind:rule=n`: a text path the mapping missed, so it can be closed), `members_scrubbed`, `withheld_blocks`. The log carries the same: `exports.build_failed` now reads `reason="summary.leak=2,evidence.leak=5,jobs.cost_word=1"` (file, rule, count); `exports.withheld` and `exports.leaks_withheld` give `file part:rule=n` (several lines when long, never cut); `exports.file_withheld`, `exports.late_replaced`, `exports.members_scrubbed`, `exports.build_crashed`. The rule for the cost word is `cost_word`, never the word: the manifest is inside the zip and the zip is scanned for the word. A canary test plants another account's name and domain in a dozen places and checks that neither the log, the manifest nor the errors carry them.
8. **Rebuild.** `POST /v1/exports` (`routes/exports._deliverables`) and the website's stale-file rebuild (`account_workspace.refresh_stale_files`) call `builder.build_all`, the same `ExportBuilder` the research run uses (`exports.wiring.build_export_builder`), on the stored rows: free, no model call. A build that stored nothing leaves nothing to clear. After the deploy the failed runs' files are rebuilt with the same call.

## Matching rules for short and common-word names (decision, with tests)

The leak scan matches a restricted term as a whole word, in any case, a dot or hyphen ends a word, terms of three characters or fewer never count. An account called "Epic" therefore refuses the word "epic" ("Jira epic stories"), not "epics" or "Epicenter". That is kept (a lowercase mention of a company name in a URL slug is a leak too); what changed is the consequence: the one cell or row that uses the word is withheld and counted, never the research. Making single-word names case-sensitive would stop "epic" and "delta" in prose but would let a lowercase company name through; it is an owner decision below.

## Tests

New, under `tests/exports/` (all fail on the old code and pass on the new): `test_build_degrades_restricted.py`, `test_build_degrades_third_party.py`, `test_build_degrades_files.py`, `test_build_degrades_diagnostics.py`, `test_build_degrades_property.py` (hypothesis: random restricted terms, tool words and the cost word planted in 1 to 4 fields, `build_all` never fails and nothing planted reaches a file), `test_build_degrades_db.py` (Postgres: other accounts, rebuild, gate, listing), helper `degrade_kit.py`.

Updated because they encoded fail-the-run on purpose:

* `test_lying.py`: `row_cap`, unknown stored code (people), invented section code, graded row without a source, narrative without a code, unregistered number in the only narrative block, Confirmed without a primary read, statement flag, hypothesis without a question, unconfirmed statement in the narrative, internal grade, missing narrative, render failure, text with nothing printable: now the row, block or file is withheld and listed. Still failing on purpose: grade outside the vocabulary, private rows, unknown section kind, lost rows, vocabulary errors, orphan codes.
* `test_spec_v2.py`: the cost word in a description and a posting on a data tool's host are withheld, not fatal.
* `test_delivery_gates.py`: `docx_rowcheck` without a converter or `pdftotext` is `not_run` (it failed closed).
* `test_properties.py`: a person with nothing printable in the name is withheld (it was a violation).

## Owner decisions

1. **G9 after the build still FAILS the run** (`agents.graph.build`). With the builder applying the same rule to every text it can only fire on markup, never on content; if it should withhold the offending file instead (PARTIAL `files_withheld`) it needs the file kind from the builder port. Not changed: it is the agent layer's own guard.
2. **The cost word and tool names are withheld, not masked.** "R&D tax credits" in a job description loses that cell's text (D-24: a quote is never altered). Masking just the word would keep the rest.
3. **A Summary is left out when no narrative can be printed** (distill wrote none, or every block was withheld by a rule); the four other files are delivered. Before, nothing was.
4. **Single-word account names stay whole-word and case-insensitive.** Case-sensitive matching for title-case names ("Epic", "Apple", "Delta") would stop false hits in prose and open a lowercase leak.
5. **`docx_rowcheck` and PDF row presence are `not_run` when the converter is unavailable or times out** (listed in the manifest and the log), instead of ending the research. A row the script finds lost still fails.
6. **An account whose own domain is a data tool's host** (the vendor itself) loses every row that links its own site (`tool_link`). Nothing else can be done without naming the tool.
7. **`archive.other_account_terms`** (`src/account_intelligence/archive.py`, the other session's file) has the same SQL defect: it lists every other account including removed ones and the same company, and its terms refuse archived text and cells. Needs `AND deleted_at IS NULL`, the same domain and the same name left out, and the account's own identity subtracted. Not touched.
8. `docs/architecture/loops.md` was regenerated by `scripts/gen_loops.py`; it also lists loops of the other session's uncommitted files.

## Commands

```
export APP_RW_PASSWORD="$(grep '^APP_RW_PASSWORD=' .env | cut -d= -f2-)"
TEST_DB=ai_test_bd PGPASSWORD=dev MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_test_bd \
  .venv/bin/pytest -m "not eval and not live" tests/exports        # one session per database
.venv/bin/ruff check <files> && .venv/bin/ruff format --check <files>
.venv/bin/mypy src
.venv/bin/lint-imports
.venv/bin/interrogate src
python scripts/gen_loops.py --check
python -m exports.docs --check
```
