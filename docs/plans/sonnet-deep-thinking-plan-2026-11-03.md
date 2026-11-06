# Plan: Sonnet in Ask thinks deeply and answers like Fable on claude.ai

Date: 2026-11-03. Status: steps 1 to 7 built and tested on 2026-11-03 (owner: "I like all the
implementations"), uncommitted; step 8 (the paid comparison) not run. See section 8.
Companion to `docs/plans/ask-insight-answers-plan-2026-11-03.md` (the answer-format plan); this
plan covers how Sonnet reasons, the other covers what the answer looks like.

## 1. What was read

- `Memory_Clean_Copy_Preview.docx`: 186 paragraphs in five sections (preferences, ABM skill and
  system history, tools, what was left out, things to confirm). Every paragraph was read.
- `abm-memory-clean.zip`: seven Markdown files (`preferences.md`, `areas/abm-system.md`,
  `areas/abmgenerator.md`, `areas/bitscale-mcp-integration.md`,
  `areas/prospect-signal-product.md`, `areas/point-and-scrape-extension.md`, `topics/tools.md`).
  Every file was read. The zip and the docx carry the same memory; the docx adds sections 4 and 5
  (what was removed, what to confirm).
- Ask's code for a Sonnet turn: the system prompt, the skill loader, the tool loop, the effort
  setting, and the 2026-11-02 staging logs.
- Anthropic's current API guide for Claude Sonnet 5.5 (thinking, effort, tool loops).

## 2. What the memory says about how Fable answers

The memory is mostly rules for deliverables, not for chat answers. The parts that shape a Fable
answer over a workspace are these:

| Memory rule | Already reaches Sonnet in Ask? |
|---|---|
| Compute numbers from the files, never estimate; canonical definitions pinned in RESTORE.md (Oct 15) | Yes, skill section 5.1. But Sonnet had no code tool on UCB, see 3b |
| A strict query with zero results: report zero, then the nearest set with the relaxed condition labelled (Oct 16) | Yes, skill section 5.1 |
| Drop lists apply to every file; counts reconcile across files (skill v1.12, Oct 16) | Yes in section 5.1, but the installed checklist says version 1.11 |
| Every count names its base | Yes, skill section 4 rule 6 |
| Every claim must appear in its cited source; label interpretations ("We believe") | Yes, skill section 4 |
| No em dashes, plain bullets, no "our read" | Yes, skill section 4 rules 2 to 4 |
| "Client" instead of client staff names; no other accounts in an account's chat | Yes, skill section 4 rules 9 and 11 |
| "Events" means conferences, summits and stage appearances, not news or filings (Oct 31) | **No.** Missing from the installed skill |
| "Owner Only" file label | The skill still says the owner's name; the loader swaps it for "the owner" at send time |
| Short chat answers ("one number instead of one whole book") | **Not in the memory at all.** Fable's short style came from claude.ai's own defaults and the chat, not from a saved rule |

So the memory itself is mostly ported already. The difference between Fable's answer and
Sonnet's answer is not the rules. It is how the model is run.

## 3. Audit: why Fable thinks deeper than Sonnet in Ask

| What Fable on claude.ai has | What Sonnet in Ask has today | Effect |
|---|---|---|
| a. A code tool over the unzipped workspace (it "ran 8 commands" for the budget answer) | On UCB, no table tools and no code tool: every 2026-11-02 turn logged the prose route, so Ask found no tables for the account | Sonnet could only search text passages, 2 to 8 searches per turn |
| b. Its thinking carries across every tool call inside one answer | `_blocks` in `chat/service.py` sends back only text and tool calls; **thinking blocks are dropped every round** | Sonnet's reasoning restarts after each tool call. This is the single biggest "deep thinking" gap |
| c. Fable's own default effort | Ask runs Sonnet at the composer's effort, medium by default (models.yaml `surfaces.chat`, token review 2026-10-20) | Less thinking per step |
| d. Starts from the Account Summary | The search cannot find one file inside a zip; the best-hit file is usually a release | Builds from the wrong file |
| e. Writes freely and short | Must end with `render_answer`: headline, up to 6 tiles, at least one table | Long, table-heavy answers |
| f. No round limit that throws the answer away | 6 tool rounds, then the reply is discarded if no `render_answer` | The $1.94 and $2.31 Fable turns were thrown away |
| g. Prompt fits the model | Rule 1a says the model's own knowledge is "not a last resort" | Anthropic's guide says Sonnet 5.5 already leans toward answering from its own knowledge in knowledge work; this rule pushes it further from the tools |

API facts that decide the design (Anthropic's Sonnet 5.5 guide):

- Thinking is adaptive and on by default. Nothing needs to "switch it on". Depth is set by
  `output_config.effort`; Sonnet 5.5's API default is `high`, and Ask lowers it to medium.
- Thinking blocks must be passed back unchanged with the rest of the assistant turn on every
  tool round. The request must stay append-only: changing the system prompt, the tools or an
  earlier message invalidates the blocks, and on accounts created on or after 2026-08-31 that is
  a 400.
- Do not ask the model to write its reasoning out as text. That invites `reasoning_extraction`
  refusals. Ask for steps to take, not thoughts to print; `thinking.display: "summarized"`
  returns a readable summary if the screen should show "thinking".
- Task budgets are for unattended loops. On an interactive surface like Ask, control cost with
  effort and `max_tokens` instead.
- Temperature is already not sent (it is a 400 on current models); nothing to change there.

## 4. What we can implement

Steps 1 to 4 need no owner decision and no paid run. Steps 5 to 8 need a decision first.

### Step 1. Keep Sonnet's thinking across tool rounds (code, free)

- In `chat/service.py`, `_blocks` keeps `thinking` and `redacted_thinking` blocks exactly as the
  API returned them (with their signature), in their original order, next to the text and tool
  calls. The same applies to the nudge round.
- Check the loop stays append-only: same `system`, same `tools`, earlier messages never
  rewritten. Today's loop already appends only; the render nudge is a text block after the tool
  results, which the guide allows.
- Tests with a fake client: a round's thinking block is sent back unchanged on the next round;
  the system and tools bytes are identical across rounds.
- This helps Fable and Opus the same way, at no extra token cost beyond what thinking already
  costs (dropped blocks were billed when produced and then wasted).

### Step 2. Make the tables and the code tool reachable (code, free)

- Step 0 of the answer-format plan: find out why UCB had no tables in Ask, and log the route
  reason and the table count on every turn.
- Offer `run_python` wherever the workspace has a zip or a workbook, not only on the tabular and
  mixed routes. This is Fable's main advantage: it counts from the files with code.
- The search filter fix and "Account Summary first" from the answer-format plan (steps 1 and 2
  there).

### Step 3. Prompt fixes for Sonnet 5.5 (code, free)

- For the procedure models (Sonnet, Haiku), change rule 1a so account facts always come from the
  workspace tools first, and add Anthropic's recommended line: use the search tool to check
  specifics that may have changed, even when confident; for researched work gather the sources
  rather than writing from training knowledge.
- Add an analysis procedure for open questions, written as steps, never as "write your
  thinking": restate the question as criteria; open the Account Summary's signal tables and
  counts; gather the rows behind each candidate point; check every figure against a file and
  mark the rest "not verified"; rank by what it means for the seller; then answer.
- Add the October 31 "events" definition to the standing rules Ask sends.

### Step 4. Never lose a paid answer to the round limit (code, free)

- From the answer-format plan, step 4: the round after the budget offers only `render_answer`;
  a reply with no `render_answer` keeps its text after the citation check.
- With effort high, raise `MAX_TOOL_ROUNDS` for Sonnet from 6 to a number set in models.yaml
  (proposed 10), because deeper reading needs more rounds. No task budget (interactive surface).

### Step 5. Sonnet at high effort (owner decision: cost)

- Per-model Ask effort in models.yaml: Sonnet `high` in Ask. Options: always high, or high by
  default with the composer chip still able to lower it.
- Cost: thinking is billed as output at $10 per million tokens for Sonnet. The 2026-11-02 Sonnet
  turns cost $0.08 to $0.42 at medium. Expect more output per turn at high; the real figure needs
  the paid check in step 8.
- `xhigh` and `max` only if the check shows a measured gain at high.

### Step 6. Show the thinking on screen (owner decision: UI)

- Optional: `thinking.display: "summarized"` so the answer panel shows short "thinking"
  summaries while Sonnet works, like claude.ai. Display changes nothing about the reasoning or
  the bill.

### Step 7. Skill text matches the cleaned memory (owner's files)

- Install the version the memory calls current (1.12: drop lists on every file, counts reconcile
  across files, recruiting boilerplate is a false positive class). The installed checklist reads
  1.11. Note: `docs/skill-1.12` in the repo is a different draft ("a queryable database per
  workspace") and is not installed either; which one is "1.12" needs the owner's word.
- Use "Owner Only" for the separate file label, as the cleaned memory does.
- Keep the personal, household and client-specific memory out of Ask, as the cleaned copy
  already does. The cleaned copy's own note applies: the skill's examples and lessons still name
  accounts, so they need the same scrub before the skill is shared.

### Step 8. Prove it (paid, only on the owner's yes)

- A free replay first: recorded tool results and a fake model check that thinking blocks are
  echoed, the summary is read first and an answer survives the round limit.
- Then one paid comparison on staging after release: the same four UCB questions from the
  claude.ai chat (posting and people counts, Salesforce listings, Bay Area people, top three
  budget insights) on Sonnet at high and on Fable. Expected cost about $2 to $6 in total, by
  the 2026-11-02 turn costs. Pass: the same counts with their base, three insights built from the
  Account Summary, "not verified" on figures no file holds, and no discarded reply.

## 5. Database access

When read access is granted, these read-only checks replace guesses in this plan:

- How many stored tables the UCB account has, and for which account id (explains step 2).
- The zip's member roles, to confirm the Account Summary is labelled as such.
- The 2026-11-02 Ask turns: model, effort, stop reason, tool calls, from the stored chat turns.
- How many UCB facts are public versus private (decides what Ask can read).

Rules: a read-only role, SELECT statements only, no writes, no restarts. Never the scratch
database the workspace-import agent is testing on, because a second test session wipes its
tables.

## 6. Order and dependencies

1. Steps 1, 3 and 4 first: they are small, free, and make every model think and finish better.
2. Step 2 with the workspace-import work, because it shares `documents/sources.py` and
   `documents/filemap.py` with that agent's uncommitted changes.
3. Steps 5 to 7 after the owner's decisions.
4. Step 8 last.

## 7. Owner decisions

1. Sonnet effort in Ask: always high, or high by default with the chip able to lower it.
2. Show thinking summaries on screen: yes or no.
3. Which skill version is "1.12", and whether to install it for Ask.
4. The facts-table question from the answer-format plan (collapsed under "Sources", or dropped).
5. A yes for the paid comparison in step 8.

## 8. What was built (2026-11-03, uncommitted)

Every change works the same for any account in any workspace; nothing names UCB or another
account.

| Plan step | Where | What it does now |
|---|---|---|
| 1 Thinking kept | `chat/service.py` `_blocks`, `_thinking_param` | Thinking and redacted thinking blocks go back unchanged on every tool round; system prompt and tool list never change inside a turn |
| 4 Round limit | `chat/service.py` `FINAL_ANSWER_NOTE` | After every round and nudge, one answer-only round (`tool_choice` none, tools unchanged); its text is the answer |
| 5 Effort, rounds | `config/models.yaml` `ask_model_settings`, `platform/llm.py` | Sonnet runs Ask at high at least; Sonnet, Fable and Opus get 10 tool rounds; config is validated |
| 6 Thinking on screen | same, plus `answers.finish`, `AnswerView.tsx` | `summarized` display; the summary rides the answer through the same scrub and shows under "How this was worked out" |
| 3 Prompt | `replica/model_answer.py` | Tools-first rule for Sonnet and Haiku; open-question procedure; rule 1a and rule 2 adjusted |
| answer shape | `replica/open_question.py`, `replica/render.py`, `replica/tools.py`, `api/contract.py`, `AnswerStructured.tsx` | Open questions answer with exactly N insights (conclusion, grade, next step); no tiles; facts table collapsed under Sources |
| 2 Data reachable | `documents/retrieve.py`, `documents/sources.py`, `replica/answers.py` | Member filters narrow retrieval itself; a file name inside a zip works as a document filter; the Account Summary of the account asked about is read whole first |
| code tool | `replica/tools.py` | `run_python` on every route for a flagged tenant |
| logging | `api/routes/ask.py` | `ask.route` logs a reason code and `tables_held` |
| fallback | `api/routes/ask.py`, `replica/answers.py` | A discarded paid reply shows only the notice and offers the same question again; the suggestion chip never names an account with no research |
| 7 Skill | `config/skill/ask-answers.md`, `agents/skill_context.py` | Ask-only rules file beside the package (open questions, events, Owner Only); the package and the session-environment file stay byte-identical, so research prompts and their cached replies are unchanged |

Not done and why:

- The version stays 1.11. A strict test forbids relabelling it 1.12 until the owner's real 1.12
  package is placed in `config/skill/archive/`.
- The skill files still name accounts and one client in their lessons and change log. Changing
  them changes every research prompt and misses the model-reply cache on re-runs, so that scrub
  waits for the owner's word.
- Why UCB had no tables on staging still needs the read-only database check (step 0).
- The paid comparison (step 8) needs a deploy and the owner's yes.
