# Ask cites the deep-dive sheet: what was built (2026-11-04)

An Ask answer drawn from a workspace table now cites the deep-dive sheet instead of the zip. A
table is a sheet when it is a deliverable workbook of a workspace zip (`deliverable_jobs` → Job
postings, `deliverable_people` → People, `deliverable_tech` → Tech stack and teams, worked out
from the member's path with `documents.workspace.classify_member`) or a research table of a
`FILE_TABLES` kind. Its citation reads "Job postings sheet" and carries
`sheet: {key, match_col, match}`. Its `url` stays empty. After the tool loop, the Ask route copies
the table's last stored `query_table` filter onto the citation (`answers.carry_table_matches`).
This happens only when the filter is one `eq`, `contains` or `icontains` test with no radius, on a
column whose header is the label of a matchable sheet column. That column must be matchable on
both the live and the file-shaped form of the sheet, so the link works whichever form the
account shows. The meta then reads "Filtered: Location contains San Francisco". Any other query
opens the whole sheet.

The browser renders "Open in the deep dive" as an in-app link to
`/accounts/<domain>/<sheet>?match_col=…&match=…` in three places: the inline chip link, the
source card and the "What this answer used" panel. The structured answer's cited cells use the
same chip.

The sheets take an allow-listed match:
- `Col.match` marks the columns a link may match on. `sheets.parse` and `file_tabs.grid_query`
  validate the match: a matchable column of that sheet, a stripped value of 1 to 200 characters,
  no NUL.
- The predicate is fixed SQL, `CAST(<column> AS text) ILIKE :match ESCAPE '\'`. The value is
  LIKE-escaped and bound.
- The match applies to the page, the total, every chip count, the cell route and the CSV and
  Excel exports. The Excel About sheet gets a "Matched" line.
- The page echoes the match as `match: {col, label, value}`.
- The sheet page forwards the match, shows a removable "Location: San Francisco" chip, and keeps
  the match on every link it builds: chips, sort headings, paging, Show more rows, tabs, the cut
  cell's fetch and the downloads.

The row's own link opens the posting's web page, as before. No model call was added.

## Staging verification

1. Deploy the api and the web.
2. Upload a workspace zip (or use an account with an imported one). Ask a count question about
   a city, for example "How many job openings are there in <city>?".
3. Check the answer's sources:
   - The [1] chip shows "Open in the deep dive", not "From your files: … (table …)".
   - The "What this answer used" panel says "Job postings sheet".
   - The source card's meta says "Filtered: Location contains <city>".
4. Click the link. The Job postings sheet opens with a "Location: <city>" chip.
   - The row count and the "All" chip equal the answer's count (when the model filtered on
     Location only).
   - Every row's Location contains the city.
5. On the sheet:
   - Click a column heading and a filter chip. The chip stays.
   - Download CSV and Excel. Both hold only the matched rows. The Excel "About" sheet has a
     "Matched" line.
   - Click the × chip. The whole table returns.
6. Open a row's posting link. It opens the web page.
7. Request `/v1/accounts/<domain>/sheets/jobs?match_col=grade&match=x`. Expect 422
   `bad_sheet_query`. Request another client's domain with a match. Expect 404.
8. Reopen an older thread. Its answers still load. Older answers have no `sheet` field and keep
   their old title.
