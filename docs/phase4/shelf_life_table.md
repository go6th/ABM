# Shelf-life table and the analyst edit procedure (D-71, generated from `config/shelf_life.yaml`)

Shelf life decides when a row is due again; it is NOT the grading half-life (hiring 21, technology
90, leadership 180, funding 365 days: confidence decay). Status: proposal (ADR-0061).

| Signal type | Cadence | Shelf life (days) | Provenance |
|---|---|---|---|
| `company_news` | weekly | 7 | SaaS 5.1 |
| `earnings_filings` | quarterly | 91 | SaaS 5.1 (reporting calendar not modelled; status: proposal) |
| `executive_speaking` | monthly | 30 | SaaS 5.1 |
| `executive_voice_earnings` | quarterly | 91 | assets/checklist.yaml |
| `financial_baseline_verified` | quarterly | 91 | assets/checklist.yaml |
| `full_profiles_senior_people` | quarterly | 91 | SaaS 5.1 priority A profiles |
| `job_board_by_lane` | weekly | 7 | SaaS 5.1 |
| `job_postings_12_months` | monthly | 30 | SaaS 5.1 |
| `job_postings_read_classified` | monthly | 30 | assets/checklist.yaml |
| `leadership_changes` | weekly | 7 | SaaS 5.1 |
| `medium_substack` | monthly | 30 | SaaS 5.1 |
| `people_by_platform` | quarterly | 91 | SaaS 5.1 senior people list |
| `podcasts` | monthly | 30 | SaaS 5.1 |
| `posts_priority_people` | monthly | 30 | SaaS 5.1 (recent activity only) |
| `review_sites` | quarterly | 91 | SaaS 5.1 |
| `technology_detections` | monthly | 30 | SaaS 5.1 |
| `vendor_communities` | quarterly | 91 | SaaS 5.1 |
| `youtube` | monthly | 30 | SaaS 5.1 |
| `facility_expansions` | none (never scheduled) | - | proposal: roadmap monitor; no cadence in the sources |
| `lead_verification` | none (never scheduled) | - | assets/checklist.yaml (null) |
| `prior_customer_status` | none (never scheduled) | - | assets/checklist.yaml (null) |
| `vendor_customer_stories` | none (never scheduled) | - | assets/checklist.yaml (null) |

## Analyst edit procedure

1. A cadence or duration change is a reviewed change to `config/shelf_life.yaml` with a `source`
   (a document) or `status: proposal`; CI validates it (`refresh.policy.parse`).
2. Setting a cadence on a `null` row starts scheduling it; nothing is scheduled by default.
3. A tenant may lengthen a shelf life within the bounds (1x to 4x) through an insert-only
   override with a reason code; shortening is refused.
4. Changes affect future due dates only; history, past deltas and past runs are never rewritten.
