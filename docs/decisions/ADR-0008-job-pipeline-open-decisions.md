# ADR-0008: Job pipeline proposals and unresolved decisions

Status: proposed, awaiting product owner. Sources: PRD sections 3.4, 4.2, Appendix A;
project job pipeline request.

## D-16: classification and full-text scope

The PRD supplies class labels and contradictory full-text phrasing. The rubric and cue
rules are proposals only (`config/posting_rubric.yaml`, generated `docs/jobs/rubric.md`).
Default in use, per the spec: `full_text_required_classes: [build, admin_owner]` (the
superset); one config line narrows it to `[build]`. A title match is always a Lead until
description evidence is read; title-only postings are never classified.

## D-17: lane keywords and aliases

The PRD names lane/platform labels but supplies no search keywords or aliases. Aliases stay
empty. So that the High row `job_board_by_lane` is searchable rather than blocked, the live
board search uses each lane's PRD-named platform names as keywords (`keyword_fallback:
platform_names`, a proposal; set it to `none` to make keyword-less lanes Not done with
`not_available_yet`). The PRD's "Data and AI" lane is a category, not a platform: it has no
lexicon terms (matching the word "Data" would match nearly every posting), so its counts show
"not searched" with a reason.

## Other proposals

Provider status is retained as unknown unless explicit status is present; live board
presence during this run makes a posting open. Undated rows are included and counted as
undated; dates before the calendar window are out of window. Derived fields redact email
and phone patterns; raw text remains in the payload store. The PII approach remains subject
to D-06/legal review. Caps in `jobs_v1.yaml` are provisional.

## Integration notes (2026-10-02 rebuild)

- The live board search is a `web_search` per keyword restricted to the verified careers host
  (the connector layer has no keyword job-board operation); open item OI-JOBS-2 asks whether a
  scrape of the careers page should replace it.
- The tracker derives `job_postings_read_classified` from ledger result counts, so a run whose
  pulls succeed but match nothing ends Done rather than the spec's None (open item OI-JOBS-1;
  the tracker never accepts a caller's count, T2).
- Out-of-window postings are counted only: never read, escalated, classified or claimed.

## Verification gaps

The connector's normalized record has no verified completeness flag. Supply one recorded
sample for each board search, job feed, and job-page read operation. Storage is migration 0012
(tested as `app_rw`, RLS catalog check returns no rows).
