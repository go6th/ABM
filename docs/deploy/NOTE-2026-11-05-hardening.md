# Research pipeline hardening after the Olympus run (2026-11-05)

Audit of the Olympus Corporation of America research (olympusamerica.com, 2026-11-04 18:44 to
19:08 UTC, run `83c6892d`, PARTIAL `files_withheld`) on the staging logs, and the fixes made from
it. Release: `__TAG__` (working-tree snapshot, uncommitted); revisions in the table at the end.

## What the logs showed

The run did not crash. It ended PARTIAL because the Summary was withheld for one dash in a
stored field (fixed and deployed the evening before, rel-5132465d). The output it did deliver was
thin for five reasons, none of them a provider or infrastructure fault:

| what | count | cause |
|---|---|---|
| Summary section 1 blocks withheld `unregistered_number` | 7 of 12 (all 4 "stands out", 2 caveats, 1 narrative) | the distill model wrote figures (sums, counts, years) that no cited row carries; the gate (B6) withholds the block; nothing told the model the rule and nothing asked it again |
| claims dropped `value_not_in_sentence` | 486 (205 interpret, 281 grading) | the extractor (Haiku 5.5) cites the right passage with the figure one sentence over, or a close paraphrase; the engine dropped the claim instead of finding the sentence that holds the value |
| candidates dropped `quote_not_in_payload` | 92 | the same, one step earlier: a quote a word off from the page |
| job postings read in full / matched a platform | 23 of 144 / 0 | the feed's description text has no completeness flag; a posting whose text named no platform was never read, so a platform named in the full posting could not be found; a page read that returns a careers shell replaced the feed text and lost its match |
| archived release pages left out of the workspace zip `third_party` | 12 | one refused word (the providers' unit word, a tool name, a restricted name) left the whole page out |

Also in the logs, not research defects: 660 ERROR lines between 20:10 and 20:40 UTC and 290
between 12:00 and 13:40 were the scheduled ticks (`/tasks/metrics`, `/tasks/sweep`, ...) printing
a traceback every two minutes while the Cloud SQL instance was stopped (409 from the connector);
2,513 anonymous `{"event": "log"}` lines in the 25-minute run were httpx request lines and the
connector events, whose JSON message the log allow-list drops; `GET /v1/chat/threads/{id}` 500s
at 18:35 (fixed the evening before).

## What changed

1. **Grading re-anchors the supporting sentence** (`grading/spans.py` `reanchor_sentence`,
   `grading/pipeline.py`): when the value is not in the cited sentence, the engine looks in the
   same stored text for the cited sentence joined with its neighbour, then for the single sentence
   that shares the most words with it; the replacement must be verbatim, within the length bounds
   and carry the value (`value_support`), so G4 holds exactly as before. The claim carries the
   flag `sentence_reanchored`; the batch logs `grading.repaired` with the count. A value the text
   never states still drops `value_not_in_sentence`.
2. **Extraction re-anchors quotes** (`agents/guards.py` `reanchor_quotes`, `agents/extraction.py`):
   a quote not verbatim in its payload is replaced by the payload's own sentence that carries the
   candidate value and shares at least 30% of the quote's words; counted as
   `repaired_quote_reanchored` in the run counters. A value the payload never states is not
   rescued (the fabricated-quote tests still pass).
3. **Distill keeps figures inside the cited facts** (`agents/distill/prompt.md`, `agent.py`): the
   prompt states the rule; code drops a claim whose figure is in neither the value nor the event
   date of a cited fact (`unregistered_number`), and asks the model once more with the reasons
   when that happened or the narrative was dropped whole; the attempt that keeps a narrative and
   the most claims is the result. The Summary gate now also counts a cited fact's event date as a
   figure the block may use (`readers/summary.py`).
4. **Jobs read more and trust the page less** (`jobs/pipeline.py`): with the caps off
   (`RESEARCH_CAPS=off`, as on staging) a feed text whose completeness is unknown is read, not only
   excerpts and matches (expect every in-window posting to be read: 144 page reads for Olympus
   instead of 23); a page read that names none of the platforms the feed text named is treated as
   a failed read, the feed text and its match stay (a lead until read).
5. **Release pages are scrubbed, not dropped** (`exports/workspace_files.py`, `builder.py`): a
   refused copy is kept with the refused words replaced by the record marker, as the run's own
   records already were; one still refused is left out as before; `exports.releases_scrubbed`
   logs the count.
6. **Ticks answer 503 when the database is unreachable** (`api/routes/tasks.py`): one WARNING
   `tasks.db_unavailable` with the error class, no traceback.
7. **Logs**: httpx, httpcore and uvicorn's access log at WARNING and above (`obs/logging.py`);
   connector events (`connector.result`, `connector.error`, `connector.cache_hit`...) now arrive
   with their fields instead of as `{"event": "log"}` (`connectors/obs.py`).

Tests: 11 new test files or cases (`tests/grading/test_reanchor.py`, `tests/agents/test_reanchor_quotes.py`,
`tests/agents/test_distill_figures.py`, `tests/jobs/test_read_repairs.py`,
`tests/exports/test_release_scrub.py`, `tests/api/test_tasks_db_unavailable.py`,
`tests/obs/test_quiet_library_loggers.py`); `tests/exports/test_fix_wave_c.py` and
`tests/connectors/test_router_product.py` updated for the new behaviour. `docs/architecture/loops.md`
regenerated. Full suite result: __SUITE__.

## Not changed, for the owner

- The run used Haiku 5.5 for extraction and planning (the config's tested arm is Opus); the
  re-anchoring repairs make Haiku's output usable but do not make it Opus.
- `tech:missing_url=2`: two technology detections without an evidence page stay withheld.
- With the caps off every feed posting is now page-read: more provider calls per research (one
  per posting), which is what the caps-off switch asks for.
- The Olympus Summary can be rebuilt free with `POST /v1/exports` on run `83c6892d`; the figure
  rule and the re-anchoring need a new research (or Resume) to take effect on that account.

## Release

__RELEASE__
