# People rubric (generated from config; do not edit)

Source files: `config/people_scope.yaml`, `config/people_rubric.yaml`, lane lexicon
`config/job_lanes.yaml`. Everything not named by the PRD is `status: proposal` (D-21 to
D-23). Config hash `c8a5c8b3e7757c06c4bfb40901348459e5da0612155896634116896a257ce101`.

## Levels (most senior first)

Order: c_level, evp, svp, vp, head, director, senior_manager, manager. In scope: c_level, director, evp, head, senior_manager, svp, vp.

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `level.director` | director | director | Director of Field Service Systems | Directory Services Analyst |
| `level.head` | head | head | Head of Enterprise Applications | Headcount Planning Analyst |
| `level.senior_manager` | senior_manager | senior manager | Senior Manager, CRM Platforms | Manager, CRM Platforms |
| `level.manager` | manager | manager | Manager, CRM Platforms | Program Lead |
| `level.vp` | vp | vice president, vp | VP, Business Applications | Valve Planner |
| `level.svp` | svp | senior vice president, svp | SVP Digital | Vice President Digital |
| `level.evp` | evp | executive vice president, evp | EVP Operations | Vice President Operations |
| `level.c_level` | c_level | chief | Chief Information Officer | Mischief Analyst |

## Bands and search passes

| Pass | Band | Levels |
|---|---|---|
| 1 | senior | c_level, evp, svp, vp, head, director |
| 2 | manager | senior_manager |

## Exclusion (beats inclusion)

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `excl.security` | security | security | Director, Information Security | Director, Salesforce Platform |
| `excl.it_compliance` | it_compliance | it compliance | Head of IT Compliance | Head of IT Operations |
| `excl.it_audit` | it_audit | it audit | Director, IT Audit | Director, IT Architecture |

## Review

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `review.compliance` | exclusion_ambiguous | compliance | Director, Compliance Systems | Director, CRM Systems |
| `review.audit` | exclusion_ambiguous | audit | Head of Audit Technology | Head of Analytics Technology |

## Priority (first match wins)

| Rule | Priority | Band | Lane required |
|---|---|---|---|
| `prio.a.senior_lane` | A | senior | True |
| `prio.b.senior_no_lane` | B | senior | False |
| `prio.b.manager_lane` | B | manager | True |
| `prio.c.manager_no_lane` | C | manager | False |

## Lanes (shared lexicon)

| Lane | Terms |
|---|---|
| salesforce | Salesforce |
| servicenow | ServiceNow |
| sap | SAP |
| tableau_analytics | Tableau |
| veeva | Veeva |
| data_ai | none yet (D-17) |

## Tenure buckets (months)

| Bucket | From | Below |
|---|---|---|
| under_1y | 0 | 12 |
| 1_to_3y | 12 | 36 |
| 3_to_5y | 36 | 60 |
| 5y_plus | 60 | open |

## Caps

| Cap | Value |
|---|---|
| cost_sample_size | 3 |
| max_excerpt_chars | 500 |
| max_extractor_calls | 60 |
| max_findings_per_person | 3 |
| max_gap_passes | 2 |
| max_pages_per_source | 10 |
| max_people_per_run | 400 |
| max_posts_per_person | 20 |
| max_profiles_per_run | 150 |
| max_text_chars | 4000 |
| max_title_chars | 200 |
| mention_queries_per_person | 2 |
| mention_run_cap | 20 |
| merge_fixpoint_cap | 2 |
| posts_budget_share | 0.25 |
| profile_batch_size | 20 |
| recent_activity_days | 90 |
| title_similarity_threshold | 0.5 |
| unknown_cost_post_cap | 3 |

Title fallback: `platform_and_level`. Profile levels: `all_in_scope`.
Association functions: IT, engineering, product. Internal-only providers: p_org.
