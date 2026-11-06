# Audit: saved Ask thread "Account Intelligence.zip" (UCB Inc, 2026-11-04)

**What the file is.** A browser "Save page" of staging Ask
(`136-81-60-29.sslip.io/?thread=02ee7576-…`), account UCB Inc (ucb.eu, built from the uploaded
`UCB_workspace.zip`), Claude Sonnet 5.5, effort High. It holds six questions and answers, each
with its reasoning trace, sources and token cost. Besides the HTML there are only Next.js
chunks, one CSS file and `__MACOSX` junk. It contains no data.

**How it was checked.** Every number and name was checked against the same uploaded files Ask
searched: `UCB_workspace.zip`, taken from `~/Downloads/UCB-Inc-everything.zip` → `Internal - your
files/`. The checks were full scans of `data/derived/people.json` (1,620 rows),
`data/profiles_full.json` (1,732 profiles), `data/derived/postings.json` (767), `live_board.json`,
and the delivered `UCB_Job_Postings.xlsx` / `UCB_People_List.xlsx` / Account Summary. The visible
defects were then traced to code.

## Verdict per answer

| # | Question | Ask said | Full scan says | Verdict |
|---|---|---|---|---|
| 1 | Open Salesforce positions | **0** open (2 closed) | **1 open**, 1 closed | **Wrong** |
| 2 | People in SF Bay Area | 1 strict, 6 wider (search floor) | **10** in Bay Area towns (+2 Central Valley) | **Undercount by 4** |
| 3 | Top 3 IT areas, budgets | CRM, Data & AI, S/4HANA; no budget figure | Matches the summary | OK, one wording slip |
| 4 | Top US Salesforce executives | 5 people | 6: misses a Head in Atlanta | **Incomplete** |
| 5 | Which SI does most work | Accenture, Hypothesis | Counts correct on the 152 base | OK, with caveats |
| 6 | People from IIT | 1 (floor) | 1 | **Correct**, but name/location said missing |

### 1. Open Salesforce positions: wrong
- There are two postings that name Salesforce:
  - **93642 Technical Lead CRM** (Health Cloud, Atlanta). Closed: filled, page 410 on 24 Oct.
  - **94240 Digital Technology Lead – Omnichannel** (Atlanta, posted 30 Sep 2026). **Open.**
- 94240 counts as open even under the answer's own rule ("open in the feed, or seen live on the
  careers board on 24 October"):
  - `live_board.json`: the board's own `Salesforce` keyword search on 24 Oct returned it.
  - `live_recapture_log.json`: captured `status: ok`.
  - `releases/2026-10-24_live_94240_…md` holds the live capture.
  - `UCB_Job_Postings.xlsx` → Jobs sheet: Status **Open**, Platforms "Salesforce, Veeva, Microsoft
    Azure, Power BI, Qlik".
- Where the 0 came from: `data/jobs_keyword_counts.json` says Salesforce `open 0, closed 2`. It
  was built from the raw feed (`jobs_12mo.json` marks 94240 `closed`), before the live board
  merge. The workspace contradicts itself, and Ask trusted the stale count file over the
  delivered workbook.
- The answer also gets the posting wrong:
  - It calls 94240 "an open commercial AI posting [that] lists Salesforce or Veeva only as 'or
    similar'". The text is "Experience with Salesforce, Veeva, Power BI, Qlik, or similar", which
    names Salesforce outright.
  - It says it "could not confirm … whether it is counted among the 2". It is one of the 2.
- "Nearest open work is relaxed, not Salesforce postings" is not a readable sentence.
- The tile "1 (0) Health Cloud postings (open)" is hard to read.
- **Correct answer:** 1 open (94240, Atlanta, live 24 Oct) and 1 closed (93642).

### 2. Bay Area people: undercount
- A full scan of `loc` finds 10 people in Bay Area towns:
  - Doris Lu (SF Bay Area)
  - Peter Lau (Cupertino)
  - Bill Feinstein (Berkeley)
  - John Derasmo (Fremont)
  - Tanya Achmetov (San Jose)
  - Nancy Hillis (Emeryville)
  - Catharine Katie Holt (Pacifica, row 132)
  - Imre Lendvay (Danville, row 789, Zogenix label)
  - David Kolsom (Petaluma, row 810)
  - Yingying Wu (Mountain View, row 1106, Zogenix label)
- Ask missed the last four.
- Borderline (San Joaquin County, not Bay Area): Ralph Catacutan (Lathrop) and Toni Freeman
  (Tracy). Ask listed Lathrop and missed Tracy.
- Ask said itself that it "could not run a table count over the 1,620-row people file". That is
  the root cause (see Defect A).
- The cited rows Ask did give are correct (people.json 787/1007/367/1465/373; xlsx 610/1131/449/
  804/1277).

### 3. Top IT areas: sound
- Postings counts match the summary: Salesforce 2, Veeva 16, SAP 43 (41 working tool),
  ServiceNow 4, all of 92.
- The CFO S/4HANA announcement (July 2024), Fabric/SKAI and "no IT budget figure in any file"
  are all right.
- Slip: "a Veeva CRM migration role was posted on October 6, 2026". On 6 Oct the Head of DT
  Patient Impact EU wrote a LinkedIn post (S28/S29). It was not a job posting.
- 4 of the 5 rows in "Facts behind the ranking" have an empty Source cell.

### 4. US Salesforce executives: one Head missing
- The five listed check out: titles, levels, Georgia locations, start dates, and the xlsx rows
  24/10/33/45.
- Missing: **Werner Raeymaeckers**, *Head of Digital Technology Commercial Services & Digital
  Content*, Head level, Atlanta. He is in the workspace's own "Salesforce, omnichannel and patient
  services" lane and meets the seniority rule (people.json row 1588). By the answer's own
  ranking he belongs above both Senior Managers.
- The headline says "four … people" and then names five. The tile says 5.

### 5. Which SI: fair, with caveats
- These match `stats.json`:
  - 152 senior tech
  - 42 with any SI
  - Accenture 11, Deloitte 9, IBM 6, Capgemini 4
  - the Accenture Belgium profile (row 1495, "returned to UCB … integration of US Jun2019 to
    Jul2020")
  - Amaris "Client: UCB" (row 101)
  - NRB 2006–2021
- Caveat: over all 1,620 people, **Deloitte (31) outnumbers Accenture (26)**. The lead holds only
  on the senior tech base. The answer should say which base it used.
- The grades are inconsistent:
  - The current Capgemini consultant (Mounsef Lahboubi, "Capgemini (2026 to now)", row 1044) is
    graded *Lead*.
  - The single current Amaris profile is graded *Probable*.
- The Capgemini row cites source 8 = people.json row 1444, Mariam Ghazanjyan. She is a Capgemini
  alum (2018–21) in a **business role**, so she is not one of the "4 of 152 senior technology
  people", and she is not the 2026-to-now profile.
- The row numbers mean different things:
  - "row 1444" is a position in the list. `rid` 1444 is a different person (Corentin Samzun).
  - The xlsx row numbers differ again (Doris Lu: json 787, xlsx 610).
  - A user cannot cross-check them.

### 6. IIT: correct count, wrong caveats
- The only IIT degree among 1,732 profiles is profile 120: B.Tech IIT (BHU) Varanasi, M.S. IIT
  Delhi "pending".
- Two other matches are not degrees:
  - "IIT" = investigator-initiated trial (row 1204).
  - A research job *at* IIT Delhi (row 1394, GM Türkiye). This one is borderline worth a
    mention.
- But profile 120 **has `fullName: "Anurag Khare"` and location Atlanta Metropolitan Area**. The
  answer said the name was "my inference from the handle" and the location was "not stated in
  the profile". The passages it searched leave those fields out.
- It also calls the result a "search floor" when it is the true total. That is a symptom of
  Defect A.

## Defects behind the answers (traced in code)

**A. The count tools are missing on this account (root cause of Q1, Q2 and Q6).**
- The model said "no table-query tool available, only list_sources and search_passages". That
  is the `prose` route (`replica/tools.py:57-63`): `describe_table` / `query_table` /
  `read_rows` / `count_tiers` come only on `tabular` or `mixed`.
- A "how many" question gets `prose` only when the workspace has no tables:
  - `router.py:210` ("no table in the workspace");
  - default `api/routes/ask.py:222-226`. Its comment already records "UCB turns logged prose
    with no table found".
- Two likely gaps:
  - `documents/repo.py:1258-1272` lists only tables with `row_count > 0`. `row_count` is set
    only when a zip part finishes cleanly (`documents/service.py:814-832`).
  - The 1,608 people and the postings that `workspace_import.py` writes into the native tables
    are never shown to Ask as tables. `research_tables.py:72-90` reads only view rows of a
    research run, and the import run has none.
- Not yet confirmed on staging. Check `SELECT table_name,row_count FROM document_tables WHERE
  document_id=<UCB zip>` and the `ask.route` log reason.

**B. Answer-format error messages make the model guess** (`replica/render.py`). These cost
extra tool rounds.
- **Cell length:** a cell over `CELL_MAX_CHARS = 320` (line 50) gets "table section N: row R must
  be K strings" (288-297). The message never mentions length.
- **Numbering:** "section N" counts every section, not tables (`enumerate(sections)`, line 213).
- **`items` vs `insights`:** the prompt says "ONE insights section of exactly N items"
  (`model_answer.py:281`). That invites `"items"`, which is a valid field of another section
  kind.

**C. Every source chip says "From your files: UCB_workspace.zip".**
- Cause: `answers.py:647` uses the document name, and the file inside the zip appears only in
  the popover meta. The same pattern is at 617, 677 and 867.

**D. The context meter misleads; cost per answer is high.**
- Tokens are summed over every tool round (`chat/service.py:457,681`), and the meter divides that
  sum by the 1M window (`ContextMeter.tsx:47`, `lib/chat-usage.ts:57`). So "81%" means 808K
  read over the turn, not how full the window is.
- Each round re-sends:
  - the skill;
  - the file map;
  - the whole Account Summary or best-hit file, up to 300K chars (`config.py:225`);
  - 6 history turns.
- Totals for the six answers: **≈2.52M input tokens, 24.6K output, $1.95**. Q5 (721K) and Q6
  (808K) cost the most, and Q6 was a one-person answer.

**E. "Refresh from web" offers are uneven.**
- File passages always have `when=None` (`answers.py:649`), so `offer_for` (1665-1686) offers a
  web refresh on any answer that cites only files.
- That is why Q2, Q3 and Q6 show it and Q1, Q4 and Q5 do not.

**F. Smaller page issues.**
- The sidebar shows "show me the people based out of San Francisco" twice.
- Updates shows "BD: research did not finish" next to two "BD: research finished" items, which
  is stale BD noise on a UCB thread.
- "168 of 200 chats left".

Not defects (checked): citation chips are separate buttons with 2px margins (they only looked
merged in a text dump), and stat tiles have a 6px gap.

## Status (2026-11-04, same day)

- **Defect A fixed.** An account with a file-import run and no materialised tables now gets its
  imported people, postings and detections as research tables (`Research: People`, `Research:
  Job postings`, `Research: Tech stack and teams`) read from the native rows
  (`replica.research_tables.native_tables`, `documents.repo.rows_source`), with `describe_table`,
  `query_table`, `read_rows` and the read plan. The tenant's current exclusions apply; a
  researched account is still read only through its materialised tables.
- **Stale count file.** The table and procedure rules now say a count in a raw data or build
  file is a cache: when a table holds the rows, the count is the table's `row_count`, and the
  delivered workbook's Status column outranks a raw feed's.
- **Defects B, C, D and E** were fixed earlier the same day (exact error messages, chip labels
  naming the file inside the zip, the meter showing the last round's prompt, the web offer
  resting on the account's refresh date).
- **Hardening (same day, second pass).** Every native-row statement carries the session tenant
  as its own predicate besides row level security (a two-tenant test reads nothing of the other
  client even with RLS off); an account whose import is still pending or running is not offered
  until it finishes; `ask.route` logs `tables_uploaded`, `tables_research` and `tables_native`;
  the Sonnet and Haiku procedure gained steps for place questions (nearby rows are kept), for
  seniority questions (`level` over every stored value) and for the imported tables versus the
  raw files behind them.
- **Defect F** (sidebar duplicate, stale BD updates) is open.
- Not yet checked on staging with the UCB account: the six questions should be asked again on
  Sonnet 5.5 at High after the deploy.

## What to fix, in order
1. **Defect A.** Make the imported people and postings queryable by Ask. This alone would have
   answered Q1, Q2 and Q6 by count, not by search.
2. **The UCB workspace data.** `jobs_keyword_counts.json` is stale against the live board and
   the jobs workbook. Ask should prefer the delivered workbook's Status column, or the import
   should recompute counts.
3. **Defect B** error wording, then the chip label (C) and the meter (D).
4. Send the Account Summary once per turn, not on every round (D).

## Side by side: Account Intelligence Ask vs personal Claude.ai (same six questions, same zip)

- The personal run is `~/Downloads/Fable Personal Claude.pdf`, chat "Salesforce open positions",
  3 Nov 2026 2:44 PM. It had `UCB_workspace.zip` attached and ran code over it ("Ran 3 commands").
- Every claim in it marked ✓ below was checked against the zip.

| # | Ask (staging) | Claude.ai (personal) | Ground truth | Closer |
|---|---|---|---|---|
| 1 Open Salesforce | 0 open, 2 closed | **1 open (94240)**, 1 closed (93642); 94217 and 94379 as adjacent | 1 open, 1 closed ✓ | **Claude.ai** |
| 2 Bay Area | 1 strict / 6 wider, "a floor" | **12 incl. Lathrop+Tracy, 10 strict**, full table with People List No. | 10 strict / 12 ✓ (all 12 names match) | **Claude.ai** |
| 3 Top IT areas | CRM, Data & AI, S/4HANA | Data & AI, CRM, S/4HANA; + ServiceNow/Veeva runners-up, budget headroom | Same three. The summary calls Data and AI "the most active spend area"; SAP 43, 14 open ✓ | Claude.ai (order follows the file, more detail) |
| 4 US Salesforce execs | 5; Werner missing | 5 priority A incl. **Werner Raeymaeckers** + 4 priority B + CDTO, CMO Mike Davis, CCO Fiona du Monceau | All names, xlsx No., places and priorities ✓ | **Claude.ai** |
| 5 Which SI | Accenture as lead (Hypothesis), 152 senior tech base; finds the Accenture UCB project profile, Amaris "Client: UCB", current Capgemini | No SI confirmed; alumni over 150 of 1,620: Deloitte 31, Accenture 26, Capgemini 18 ✓; NTT DATA "via NTT Data" ✓; S/4HANA lead runs own consultancy ✓ | Both counts are right on their base. Claude.ai **misses** Amaris (current, "Client: UCB"), the Capgemini 2026-now consultant and the Accenture UCB project profile, and says on-site traces are "thin" | **Ask** (better direct evidence), but it should name its base |
| 6 IIT | 1, "a floor"; name "inferred", location "not stated" | 1: **Anurag Khare**, Atlanta, priority A; same two false positives ruled out; 68 profiles empty ✓ | 1 ✓ | **Claude.ai** |

**Score.** Claude.ai is right or more complete on 5 of 6; Ask is better on 1 (Q5).

**Why they differ.**
- Claude.ai ran code over the whole file: full scans, exact counts, and the delivered workbooks'
  Status column.
- Ask on this account had search only (Defect A). It sampled passages, got trimmed profile
  chunks (no `fullName`/location), and trusted the stale `jobs_keyword_counts.json` over
  `UCB_Job_Postings.xlsx`.
- Where search helps, Ask did better. In Q5 it surfaced free-text profile lines ("Client: UCB",
  "returned to UCB") that a column count does not see.

**To reach parity.**
- Fix Defect A, so Ask gets `query_table` / `count_tiers` over the imported people, postings and
  profiles.
- Prefer the delivered workbook over raw count files.
- Index profile passages with name and location.
- Keep the passage search for evidence questions like Q5.

## Fixes applied (2026-11-04, uncommitted, not deployed)

**Root cause, confirmed.**
- Staging logged `route=prose reason=no_tables tables_held=0` on every UCB turn
  (`ask.route`, 2026-11-03 19:29 to 2026-11-04 08:22).
- The zip was uploaded **unbound**: it parsed to `parsed`, then indexed after the account was
  confirmed.
- `documents.repo.bind_account` re-pointed the passages and redactions, but **not
  `document_tables`, `document_table_rows` or `document_members`**.
- So all 57 tables kept no account, and `tables_for` (filtered by account) never listed them.
- Locally, a bound upload of the same zip gives 57 tables, which is why it worked in tests.

| # | Fix | Where | Test |
|---|---|---|---|
| A | `bind_account` also moves the table store and zip members | `documents/repo.py` | `test_a_zip_uploaded_unbound_keeps_its_tables_when_the_account_is_confirmed` |
| A′ | Tables already left unbound (staging's UCB zip) count under the document's account: no migration, no re-upload | `repo.tables_for` | same test, legacy case |
| 1 | File map: delivered tables first, tagged `[DELIVERED FILE]`; raw pulls tagged "may be stale"; rule: count and filter from delivered files, delivered wins | `documents/filemap.py` | `test_a_workspace_package_lists_its_delivered_tables_first_and_marks_the_raw_pulls` |
| 6 | A long row's later pieces (education, experience) carry the row's label (name, title, location), and the cite shows it | `documents/chunking.py` | `test_a_long_row_is_split_into_exact_parts_that_join_back` |
| B | Format errors name the column, the cell length, the limit and "sections count from 1"; `items` vs `insights` is pointed out; the prompt says "`insights` array" | `replica/render.py`, `model_answer.py` | `test_the_shape_check_says_exactly_what_to_fix` |
| C | Chips name the file inside the zip ("UCB_People_List.xlsx in UCB_workspace.zip") | `replica/answers.py` (`file_label`), `documents/retrieve.py` (`Passage.member`) | `test_a_source_from_inside_a_zip_names_its_own_file` |
| D | The meter shows the **last round's prompt** (`context_tokens`, new on `ChatUsage`, done frame and stored answer); "read" totals are shown apart; older turns show tokens and no false % | `chat/service.py`, `api/routes/ask.py`, `web/lib/chat-usage.ts`, `ContextMeter.tsx` | `web/tests/unit/token-meter.test.tsx` |
| E | A file-only answer offers the web by the account's refresh age, not "undated", so the offer is consistent | `answers.offer_for` | `test_an_answer_from_uploaded_files_offers_the_web_by_the_accounts_refresh_age` |
| 5 | Evidence questions ("based on your findings", "does the most") keep passage search beside the tables (mixed route) | `replica/router.py` | router suite |

**Replay of the six questions on the real UCB zip.**
- Setup: unbound upload → confirm → Ask's own table tools. No model calls.
- Route: every question now gets the table tools (tabular or mixed, not prose).

| # | Table tools return | Claude.ai |
|---|---|---|
| 1 | 94240 **Open**, 93642 Closed | same |
| 2 | 1 exact + 8 within 50 mi | 10 (+ Emeryville) |
| 4 | Barnes, Simmons, **Raeymaeckers**, Akanksha P., Amisha N. (+ Johnson via certifications) | same set |
| 5 | Deloitte 31, Accenture 26, Capgemini 18, any SI 150 | same |
| 6 | Anurag Khare, Atlanta | same |

**Still open.**
- **Emeryville** (pop. about 13k) is not in `config/places.json` (GeoNames `cities15000`), so the
  nearby search counts it only as "unplaced". The fix is to rebuild with `cities5000`, which
  needs the extract downloaded.
- Sending the Account Summary once per turn instead of every round (cost).
- A paid re-ask of the six questions on staging after deploy (owner's call; see
  `no-repeat-spend`).
