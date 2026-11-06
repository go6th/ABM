# Plan: Ask answers open questions the way Claude does

Date: 2026-11-03. Status: plan only. No code written, nothing committed, no paid run made.

## 1. The problem

On the UCB workspace, "What are the top three budget insights?" gets two very different answers.

Claude on claude.ai, with the same zip, opened the Account Summary, built three seller conclusions
from its Tier 1 "budget proximate" signals and platform counts, and finished in a few paragraphs.

Ask started from the financial releases and profile figures, answered with six headline numbers,
a long facts table and next steps, and on one Fable turn produced nothing usable at all.

## 2. What the code and the staging logs show

Staging `ask.route` and `ask.model_call_reset` events, 2026-11-02 16:49 to 19:23 UTC:

| Turn | Model | Route | Searches | Cost | Outcome |
|---|---|---|---|---|---|
| 19:04 | Sonnet 5.5 | prose | 4 | $0.26 | answered |
| 19:06 | Fable 5.1 | prose | 4 | $0.81 | answered |
| 19:11 | Sonnet 5.5 | prose | 8 | $0.34 | answered, said the tool budget ran out |
| 19:12 | Fable 5.1 | prose | 9 | $1.94 | reply discarded, stop_reason tool_use |
| 17:20 | Fable 5.1 | prose | 11 | $2.31 | reply discarded, stop_reason tool_use |

Added after the 2026-11-03 audit of yesterday's commits: every one of those turns ran on api
revision 00111, which has the d3831549 code. That code turns a prose route into mixed, with the
table tools, whenever the workspace has any table. All eleven turns from 18:54 to 19:23 UTC still logged route prose, so
Ask found no tables for UCB at all. Sonnet and Fable therefore never had the table tools,
`run_python` or the Sonnet reading procedure, only passage search. The zip's workbooks did index
as tabular members on 2026-11-02 (14:52 UTC), so why no table reached Ask is still open. `ask.route`
does not log the route reason or the table count, and a direct database check was declined
before. Step 0 below covers it.

Five causes, in order of impact:

1. **The Account Summary is hard to find by search.** In `src/documents/sources.py`, `search`
   pushes a `document` filter into retrieval but applies a `member` filter only after it. A
   search filtered to the Account Summary inside the zip first takes the top 50 passages of the
   whole zip, then drops every one not from that member. For a budget question the releases and
   profiles win those 50 places, so the filtered search returns nothing. A `document` filter with
   the summary's file name never matches either, because the document is the zip.
2. **Nothing makes Ask read the Account Summary first.** `take_whole_file` in
   `replica/answers.py` sends the member of the best search hit whole. For a budget question that
   is a release, not the summary. Zip members already carry a role, and the summary has its own
   (`deliverable_summary` in `src/documents/workspace.py`), so the code can find it without search.
3. **Every answer must carry a table.** The system prompt rule 2 in `replica/model_answer.py`,
   the `no_table` error in `replica/render.py` `check_shape`, the render nudge in
   `chat/service.py` and `ensure_table` all force a facts table, even on a judgment question.
   This was the owner's rule of 2026-10-17. Up to six headline number tiles come with it.
4. **Running out of tool rounds throws the answer away.** `chat/service.py` allows six tool
   rounds. After that the model gets a `tool_budget_used` error and up to two nudges. Fable kept
   searching, never called `render_answer`, and the paid reply was discarded. Cause 1 is why it
   kept searching.
5. **The fallback shows placeholder rows.** After a discarded reply the local draft lists
   whole-file rows whose text is only "UCB", then `ensure_table` tables them. The notice is
   honest but the rows are useless. The "What do we know about 6th?" chip comes from
   `suggestions` in `replica/answers.py` picking the first account when nothing else fits.

What is already in place: the skill's standing user rules (section 4 of
`config/skill/archive/references/session-environment.md`) already reach every Ask answer. They
cover no em dashes, plain bullets, "every count names its base" and "Client" in place of names.
Your claude.ai memory is mostly ported already. What the skill lacks is a recipe for open
questions.

## 3. Owner decisions needed before step 3

1. **Facts table on open questions.** Options:
   - A (recommended): keep building the facts table for traceability, but show it collapsed
     under a "Sources" toggle for open questions. The 2026-10-17 rule keeps its purpose.
   - B: drop the table on open questions entirely.
   - C: keep it as now.
2. **Headline number tiles on open questions.** Recommended: off. The insights carry their own
   numbers.
3. **Which questions count as open.** Recommended: summary, "top N", insights, "what matters",
   budget, priorities, "why", "what should I". Lists, counts and lookups keep today's format.

## 4. The changes

Steps 1, 2, 4 and 5 need no decision and can start now. Step 3 waits for section 3.

### Step 0. Find why UCB had no tables in Ask (free)

- Log the route reason and the number of tables found in `ask.route` (`tables_held` is already an
  allowed log field, so the allow-list needs no change).
- Locally, index a workspace zip shaped like UCB's and check that `list_tables` returns its
  workbooks for the same account the Ask thread uses.
- On staging, with your yes, one read-only count of the UCB account's stored tables.

### Step 1. Make the member filter work (free, code only)

- Push a `member` filter into retrieval, the same way the `document` filter is pushed in today
  (`documents/retrieve.py` takes a member list next to `within`).
- Let a `document` filter also match a member name inside a zip, so "UCB_Account_Summary.docx"
  finds that member.
- Tests first in `tests/documents`: a zip with many release passages and one small summary
  member; a member-filtered search must return the summary passages. It fails today.

### Step 2. Read the Account Summary first on open questions (free, code only)

- New open-question detector next to `classify` in `replica/answers.py`, using the word list
  from decision 3. It is a fixed word list, with no model call.
- For an open question on the prose or mixed route, `take_whole_file` loads the
  `deliverable_summary` member whole, if it fits `ASK_WHOLE_FILE_MAX_CHARS`. The best search hit
  becomes a second whole file only when the budget allows. It goes in the cached file block, so
  a follow-up question pays cache price for it.
- The file map marks it "read in full", so the model does not search it again.
- Tests: an open question on a workspace zip sends the summary whole; a list question is
  unchanged.

### Step 3. A short answer shape for open questions (after decisions 1 to 3)

- `render_answer` gets an `insights` section kind: each insight is a conclusion of at most three
  sentences, one grade, one next action, and its citations. `check_shape` accepts it in place of
  a table for open questions only, and caps the count at what the question asks ("top three"
  means three).
- `RENDER_RULES`, the render nudge and system prompt rule 2 get the matching exception for open
  questions. Every other question keeps "every matching row".
- Under option A: `ensure_table` still builds the facts table, flagged `collapsed`, and
  `web/components/ask/AnswerView.tsx` shows it behind a "Sources" toggle. The stats tiles are
  not shown for open questions.
- Tests in `tests/replica/test_render.py`: the `no_table` test keeps passing for list questions;
  new tests cover an open question with insights and no table, and an insight count over the
  limit.

### Step 4. Never throw away a paid answer for lack of rounds (free, code only)

- In both tool loops of `chat/service.py`, the round after the budget is used offers
  `render_answer` as the only tool. Forced tool choice stays off, because it conflicts with
  extended thinking. Removing the other tools gives the model nothing else to call.
- If the model still writes no `render_answer`, keep its last text as the answer, run the same
  citation check, and mark it "finished without the full format". Only an unknown citation
  still discards it.
- Log `ask.tool_budget_used` with the round count, so the cause shows in staging logs.
- Tests: a fake provider that keeps calling `search_passages` ends with a rendered answer, not
  a reset.

### Step 5. Honest fallback and the "6th" chip (free, code only)

- After a discarded reply, show the notice, the cost and a "Try again" button. Do not list
  whole-file rows whose text is only the account name, and do not build a facts table from
  them.
- `suggestions` never offers the tenant's own company. With nothing better it offers nothing.
- Tests for both.

### Step 6. Skill text for open questions (owner's file)

Add a subsection 5.4 "Open questions: insights, priorities, budget" to
`config/skill/archive/references/session-environment.md`. Only Ask reads section 5, so research
runs are unaffected. Proposed text, about eight rules:

1. Start from the Account Summary: its Tier 1 signals, platform counts and coverage tracker.
   Then go to the underlying files only to check or add.
2. "Top N" means exactly N.
3. Each insight is a seller's conclusion: what it means for the deal, one grade, one next action.
4. Every count names its base, for example 43 of 92 platform postings.
5. A figure you cannot trace to a file is written "not verified". Never repeat it as fact.
6. When two files disagree, say which and why. The $2B Georgia figure is the current example.
7. Hiring for a platform shows skills demand, not budget size. Label any budget reading of it a
   Hypothesis.
8. Capital or manufacturing spend is not platform spend unless a file links them.

The skill 1.12 draft in `docs/skill-1.12` is separate and stays uninstalled. Rule 6 of 5.1
(fix the wrong file) applies to deliveries, not to answers.

## 5. Checking it

Free checks, run before any paid turn:

- The unit tests above, run with `-m "not eval and not live"` on a private scratch database.
- A local replay of the UCB question with a recorded fake provider. It checks that the summary
  is sent whole, the tool list narrows after the budget, and an insights answer renders.

Paid check, only on your yes:

- The same UCB budget question once on Sonnet 5.5 and once on Fable 5.1 against staging after
  release. Expected cost about $0.30 for Sonnet and $0.80 to $2.00 for Fable, by the 2026-11-02
  turns. Pass means three insights built from the Account Summary's Tier 1 rows, every count with
  its base, "not verified" on the operating expenses, net debt and H1 2024 quote figures, and no
  discarded reply.

## 6. Depends on the workspace import work (another agent, same folder, uncommitted)

The workspace import in `docs/plans/workspace-import-plan-2026-11-03.md` is being built in this
same folder and branch, uncommitted. Three links to this plan:

- Step 2 needs the member roles in `src/documents/workspace.py` and the role column that
  `list_sources` now returns. Both are that work's uncommitted files, so this plan's step 2
  ships with it or after it.
- Its phase 3 turns the Account Summary's signal tables into graded facts. Ask reads only facts
  marked public (`load_workspace` in `replica/answers.py`), and imported facts are private
  unless the file is marked authoritative. That plan's open decision 1 therefore also decides
  whether Ask sees the Tier 1 signals as numbered, graded rows. If it does, step 2 here gets
  simpler: the signals arrive as rows, and the whole summary file is a backup.
- Its imported postings and people go to the native tables, but Ask's research tables read only
  materialized views of delivered runs (`replica/research_tables.py`). From a code search, not
  traced end to end: an imported workspace does not become an Ask research table by itself.
  Only the raw workbook copies in `document_tables` reach Ask's table tools.

Steps 1 and 2 touch `src/documents/sources.py` and `src/documents/filemap.py`, which that work
also changes. Start them only when that agent has finished with those files.

## 7. Not changing

- Lists, counts, people and posting lookups keep "every matching row" and today's tables.
- No model or effort change, no output cap (the 2026-10-20 order stands), no fallback model.
- The web lookup stays as the owner set it, used when the model decides.
- Nothing is committed or deployed until you say so.
