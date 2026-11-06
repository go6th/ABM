# Ask cites the deep-dive sheet, not the zip (owner, 2026-11-04)

**Owner's request:** a question such as "how many job openings are there in San Francisco?"
answers with the count. Its [1], [2] should open the deep-dive Jobs sheet, filtered to those rows.
From the row, the user opens the posting's web link.

## Today (read-only survey, to be checked line by line before editing)

- **Ask** is `POST /v1/chat/ask` (`api/routes/ask.py`). Its tables come from `T.list_tables`
  (the zip's own `document_tables`) and `RT.research_tables`.
- **Answers about a zip** are computed over the zip's workbook tables, not over the imported
  `job_postings` / `people` / `tech_*` rows.
- **A table citation** is `answers.table_rows`: key `table:<id>`, title "From your files: <zip>
  (table <sheet>)", `url=None`. The browser gets `AnswerSource` (`api/contract.py`) with no sheet
  field, and `source_link` / `safeLink` drop any non-http(s) address.
- **The sheet page** (`web/app/(client)/accounts/[domain]/[sheet]/page.tsx`, `SheetView.tsx`)
  reads only `tab, limit, offset, filter, sort, dir`.

## Change (no new model call)

1. **Sheets get an allow-listed match.**
   - `Col.match` marks a column a link may match on.
   - `Query.match` holds `(Col, value)`; `sheets.parse` and `file_tabs.grid_query` validate it.
     The value is bounded to 200 characters.
   - The predicate is fixed SQL, `s.<col> ILIKE :match` (escaped, bound). It applies to the page,
     the chip counts, the total, and the Excel and CSV exports (all through the same `Query`).
   - The routes take `match_col` and `match`.
2. **Tables map to sheets.** A cited table whose member is a deliverable workbook maps by
   `documents.workspace` role: `deliverable_jobs` → `jobs`, `deliverable_people` → `people`,
   `deliverable_tech` → `technology`. A research table maps through `FILE_TABLES`.
3. **The citation carries the query's filter.** It copies the cited table's last stored query
   filter onto the citation, when it is an equality or contains test on a column the sheet allows
   (location, title, status, ...).
4. **`AnswerSource` gains `sheet: {key, match_col, match} | null`.**
   - The title reads "<Sheet title> sheet" (e.g. "Job postings sheet"), plus the match when there
     is one.
   - `relabel_answer` keeps the field. Contract, OpenAPI and `api.gen.ts` are regenerated.
5. **Web.**
   - A source with `sheet` renders a link to `/accounts/{domain}/{key}?match_col=…&match=…` in the
     chip card and the "used" panel.
   - The sheet page forwards `match` and shows a removable "Location: San Francisco" chip.
   - The row's existing web link does the last hop.

## Tests

- Unit tests for `parse` match validation (unknown column refused, value bound).
- A DB test: the Jobs sheet with `match` returns only the matching rows, and the export matches.
- An Ask test: a table answer about an imported workspace cites `sheet=jobs` with the match.
- Web unit tests: the citation link and the sheet page match chip.
