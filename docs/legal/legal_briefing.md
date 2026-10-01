# T6 — Legal & compliance briefing (Phase 0)

Status: **drafted for counsel review** (V5, V7, V10 in the verification register are owned by
counsel/founder). Nothing in this file is legal advice; it is the internal briefing the
counsel memo (placeholder below) will confirm or correct.

## 1. Scope of processing

The platform processes, for business-account research only:

- **Company-level data** — legal name, domain, HQ, industry, size, funding, leadership roles,
  hiring signals, tech stack, news. Lawful basis: legitimate interests (B2B), balancing test
  documented per processing purpose.
- **People-level data** — names and role titles of company officers only, as they appear in
  public professional profiles/announcements (no private contact data, no behavioral or
  special-category data). This is the DPDP/GDPR-sensitive class (V10).
- **Client inputs** — CRM exports and uploaded docs (Phase B): processed as processor for the
  client (controller), under the DPA template below.

## 2. Broker data resale terms (V7)

Blocking questions before any broker feed reaches a client artefact:

1. Does the licence permit **resale/redistribution of derived data** to third parties
   (our clients), or only internal use?
2. Field-level restrictions (e.g. revenue bands may be display-only)?
3. Attribution or logo requirements in client artefacts?
4. Rate limits and per-seat vs per-call pricing — feed into T13 economics.

Until each contracted broker's terms are confirmed in writing: broker-derived facts may power
internal scoring but client artefacts must be producible from official/registry/news sources
alone (this constraint is already enforced by the I7 gate design).

## 3. Web scraping terms (V5-adjacent)

- Respect robots.txt and published ToS for every fetched origin; per-domain rate limits
  recorded in the fetch ledger.
- No circumvention of logins, paywalls, or anti-bot controls; where a source requires terms
  acceptance (e.g. job boards with API terms), use the sanctioned API tier only.
- Copyright: store extracted **facts with citations**, not full-page mirrors; retention
  limited to the raw-document TTL (30 d) after which only fact + citation + hash persist.
- GDPR/DPDP note: public company pages and official filings are in scope; personal data
  minimisation applies to anything beyond officer names/roles.

## 4. People data — DPDP & GDPR (V10)

- **DPDP Act (India):** process only for the notified lawful purpose; honour notice and
  consent principles of the client as controller for client-supplied data; erasure requests
  flow through the client (data principal → client → us as processor).
- **GDPR (EU clients):** Article 6(1)(f) legitimate interests for officer names/roles with a
  documented balancing test; Article 14 transparency duty when data is not collected from the
  data subject — mitigated because officers' business contact context is public; still record
  the Art. 14(5) assessment in the counsel memo.
- **Cross-border:** briefs are generated in the primary region (see ADR-011); if a client
  requires EU/IN residency, that is a silo-tier decision deferred with trigger (C5).
- **Retention:** officer facts expire with the leadership half-life (180 d) unless
  re-verified; erasure on tenant deletion.

## 5. Vendor retention / zero-retention terms (V5)

For every LLM/embedding/speech vendor used in client-data processing, obtain written:

- zero retention or ≤ 24 h abuse-monitoring retention only;
- **no training on our or our clients' data**;
- subprocessor list with change notification (feeds the register below);
- data-processing terms consistent with the DPA template.

Vendors failing any item are excluded from client-data processing paths regardless of price
(ADR-010 model routing must respect this list).

## 6. DPA posture

- The DPA template (dpa_template.md) is the master client-facing agreement; sign only with
  the counsel-memo changes incorporated.
- Subprocessor onboarding requires: security review + DPA back-to-back + register entry
  (subprocessor_register.csv) + client notice per the DPA notice period.

## 7. Open items

| Ref | Item | Owner | Register link |
|-----|------|-------|---------------|
| L-1 | Broker resale/licence confirmations (per broker) | founder | V7 |
| L-2 | Zero-retention vendor confirmations (per vendor) | founder | V5 |
| L-3 | DPDP/GDPR applicability memo for people data | counsel | V10 |
| L-4 | Art. 14(5) balancing test appendix | counsel | V10 |
| L-5 | DPA redlines vs template | counsel | dpa_template.md |
