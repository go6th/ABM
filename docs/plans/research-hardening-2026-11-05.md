# Research hardening, 2026-11-05

Why: four Olympus researches on staging on 2026-11-05 cost about $200 in provider calls
(eaf83c3e $93, 12692e1a $40, d120ed36 $4, 8ed9911a $61). None crashed. 8ed9911a finished every
step and passed the quality gate, yet it ended PARTIAL, and it paid about $61 again for work the
earlier runs had done.

## The rules

1. **A run is COMPLETED when it did everything it could do.** A High row that only the client can
   unblock is a stated gap ("complete with gaps"), not a stop: people data waiting for consent
   (`consent_required`), a follow-up search nobody approved (`pull_not_approved`), mail and notes
   not switched on (`no_internal_access`), a company with no careers site (`careers_site_not_found`).
   Real execution failures still end PARTIAL (`source_failed`, `not_available_yet` for a row never
   reached, the jobs row's own reasons). Billing is unchanged: COMPLETED and PARTIAL settle the
   same real spend. Config: `config/tracker_v1.yaml` `high_reasons_allowed_for_completed`.

2. **A row is Partial only for work a later pass could still do, and says why.** The job postings
   row said "Execution cap reached" with every cap off. It now says "Some matching postings were
   not read yet", "The job feed did not return every posting" or "Posting counts did not add up".
   A posting whose one page read was spent and failed (closed posting, careers shell, provider
   error) is final, as the gap pass already treated it; it no longer keeps the row Partial on
   every run. A read the run's budget or call cap refused before any call is not spent: it stays
   open and the row Partial. A careers page the research found but could not verify keeps
   blocking (`not_available_yet`); only a company with no careers site is a stated gap. Code:
   `src/jobs/pipeline.py` `_escalate`, `_feed`, `_finish`; `tracker/derive.py` `partial_reason`.

3. **Breadth follows the source, at any company size** (skill phase1-pull.md: "page until the
   reported count is reached", "load every matched row"). A fixed count (40 page reads, 2,000
   feed items, N people) fits one size of company and cuts a Salesforce-size one short on every
   run. Pages, items, page reads, extractor calls, people, profiles, posts and reads per search now
   follow the source to its end in every mode. Time and spend stay with `RESEARCH_CAPS`: the slice
   count, provider timeouts and retries, the kept page length, the run's dollars, tokens, credits
   and call cap. Repeated cursors and identical pages and the no-progress cut-off (3 slices without
   a new paid call or model reply) still bound a run.
   `RESEARCH_BREADTH=capped` restores the fixed counts. Code: `dev_limits.breadth_follows_source`.
   A retry bound is not breadth and is never lifted: `mention_queries_per_person` (query variants
   per name) was in the lifted list, so with the caps off a mentioned name with no match could be
   searched up to a million times, each a paid people search. It stays at its configured 2.

4. **Never pay twice for the same company.** Model replies are reused across runs of the tenant
   for 7 days, but the key held the account id (every prompt cites
   `accounts/<account>/runs/<run>/raw/...`). Deleting the company and adding it again through an
   upload gave it a new id, so 8ed9911a paid all 497 model calls again. The account id is now
   normalised out of the key like the run id; a reply logged before the change is still found
   under its old key (and logged again under the new one). Provider calls were already keyed on
   content only.
   Code: `src/llm/reply_cache.py`.

5. **Say what a research cannot do before it is paid for.** The new-research and refresh estimates
   list the gaps (today: people consent not granted) with where to fix them, above Proceed. The run
   is not refused; a refresh shows it only for the people and full scopes; a failed check shows no
   warning rather than breaking the estimate. Code: `api/research_gaps.py`,
   `web/components/UsageProceed.tsx`.

## Not changed, needs the owner

- With the caps ON, the money cap is the only stop left. For a large company the estimate shown
  before Proceed must scale with the company (today it averages earlier runs,
  `replica/usage_estimate.py`); otherwise a Salesforce-size research stops at a small company's cap.
- 8ed9911a also made 348 new searches and 508 new job-page scrapes: its domain or company name
  differed from the earlier account (two account ids within a minute). New addresses are new work;
  to reuse a research, refresh the same account instead of deleting and adding it again.
- `config/models.yaml` prices Haiku at $0.10 / $0.50 per million tokens; if that is a typo, model
  spend is under-reported.
