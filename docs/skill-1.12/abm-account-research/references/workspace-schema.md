# Workspace database schema

`{Account}_phase1.sqlite` is built by `assets/build_sqlite.py build <phase1 folder>` from the final phase 1 files. It holds the whole package, so a question is answered by SQL over the full table. Open it with `sqlite3 {Account}_phase1.sqlite` or Python's `sqlite3`. `README_sqlite.md` beside it lists the tables of this account with their row counts.

## How to answer from it

1. Check the `summary_*` tables first. When the question is not covered, query the base tables.
2. Run every figure that goes into an answer with `python assets/build_sqlite.py query <db> "<purpose>" "<SELECT ...>"`. It runs the query read only, prints the base table and its row count, and logs the query as `Q<n>`.
3. State the number with its base and its id: "438 of the 7,148 postings in `jobs_12mo` (Q12)".
4. `python assets/build_sqlite.py log <db>` prints the query log as the table that becomes the Summary's Query log section.
5. Never answer from `documents` or `text_lines` by reading a few rows. They are for finding wording (`WHERE text LIKE '%...%'`), not for counting.

## Tables

| Table | One row is | Notes |
|---|---|---|
| one per `data/*.json` or delimited `data/*.txt`, named after the file (`people_grid_rows`, `profiles_full`, `jobs_12mo`, `techstack_detections`, `teams`, `news_events`, `connections`, `company_record` ...) | one record of the file | `_row` is the record's number in its file, `_source` the file name. A record that holds a nested object is flattened one level (`tech_primary`); deeper values are JSON text, read with `json_extract(column, '$.key')`. A file that is one record has one row. |
| `documents` | one release, note or the pull log | `file`, `kind` (release, note, log), `date` and `slug` from the release name, `text` in full |
| `text_lines` | one line of a text file that is not a grid | `file`, `line_no`, `text` |
| `files` | one source file | `table_name`, `rows_in_file`, `rows_in_db` (equal, or the build stops), `count_reported` and `count_saved` for feeds that report their own count |
| `columns` | one column of one table | `type`, `non_null`, `example` |
| `query_log` | one query | `purpose`, `base_table`, `base_rows`, `sql`, `result` (the first 20 rows as JSON) |

## Summary tables

They count the rows as stored. Duplicates and reposted requisitions are included; say so, or count `DISTINCT` on the requisition column in a logged query.

| Table | Columns | Built from |
|---|---|---|
| `summary_counts` | `table_name`, `rows` | `files` |
| `summary_people_by_location` | `country`, `city`, `people` | the people grid's location column (city is the first part, country the last part after a comma) |
| `summary_people_by_school` | `school`, `people` | the school names inside the profiles' education field (`people_schools`) |
| `summary_people_by_technology` | `technology`, `people` | people whose profile text names the technology as a whole word (`people_technology`): Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday, plus any `--keywords` |
| `summary_tech_mentions_by_status_month` | `technology`, `month`, `status`, `postings` | postings of the jobs file that name the technology as a whole word, by first seen month and posting status (`job_technology`) |

Helper tables (`people_locations`, `people_schools`, `people_technology`, `job_technology`) hold one row per person or posting with the fact the summary groups on, so a summary can be rechecked row by row.

A keyword count is a lead, not a finding (SKILL.md). The summary tables say how many rows name a word; reading the postings decides what they mean.

## Example queries

```sql
-- Postings that name Salesforce, by status, with the base
SELECT status, count(*) AS postings FROM job_technology
WHERE technology = 'Salesforce' GROUP BY status;          -- base: jobs_12mo

-- People in the grid in one city
SELECT count(*) FROM people_locations WHERE city = 'San Francisco';   -- base: people_grid_rows

-- Distinct requisitions instead of rows (name the column the file uses)
SELECT count(DISTINCT requisition_id) FROM jobs_12mo WHERE title LIKE '%Salesforce%';

-- Wording: where a release names a vendor
SELECT file, date FROM documents WHERE kind = 'release' AND text LIKE '%MuleSoft%';
```

## When the database cannot answer

If a table, a file or a column the question needs is not in the database, say which one. A missing file is a gap in the pull to log and fix, not a reason to answer from the part of a file that is visible.
