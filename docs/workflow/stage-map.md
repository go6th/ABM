# Workflow v1 coverage ownership

This mapping is derived from the checked-in checklist labels and the PRD process
description in the account-intelligence skill references. The original PRD Appendix A
was not present in `docs/sources/`; mappings beyond the five Pass 1 rows are therefore
proposals pending comparison with that appendix.

| Checklist ID | Checklist row | Pass | Owner | Basis |
| --- | --- | ---: | --- | --- |
| P1.01 | Prior customer status, past interactions, prior pursuits | 1 | `pass1_relationship` | Pass 1 relationship history |
| P1.02 | Connections, alumni overlaps, former colleagues | 1 | `pass1_relationship` | Pass 1 overlap research |
| P1.03 | Platform vendor account team | 1 | `pass1_relationship` | Pass 1 vendor relationship |
| P1.04 | Profiles and posts of key contacts | 1 | `pass1_relationship` | Pass 1 obvious contacts |
| P1.05 | Basic fit check | 1 | `pass1_relationship` | Pass 1 fit check |
| P2.01 | Company news and press releases | 2 | `news` | Step 4 news |
| P2.02 | Earnings releases and filings | 2 | `baseline` | Step 3 company baseline |
| P2.03 | Leadership changes, last six months | 2 | `baseline` | Step 3 leadership page |
| P2.04 | Job board search by lane | 2 | `jobs` | Step 5 job boards |
| P2.05 | Job postings read and classified | 2 | `jobs` | Step 5 postings |
| P2.06 | Job postings, last 12 months, open and closed | 2 | `jobs` | Step 5 postings |
| P2.07 | Technology detections | 2 | `tech` | Step 6 detections |
| P2.08 | People by platform | 2 | `people` | Step 7 title search |
| P2.09 | Full profiles of senior people | 2 | `people` | Step 7 profile pulls |
| P2.10 | Posts and comments of priority people | 2 | `people` | Step 7 priority A activity |
| P2.11 | Executive voice from earnings calls | 2 | `baseline` | Step 3 transcripts |
| P2.12 | Financial baseline verified | 2 | `baseline` | Step 3 releases and filings |
| P2.13 | Vendor customer stories | 2 | `name_searches` | Step 8 name searches |
| P2.14 | Verification of items graded Lead | 2 | `verification` | Step 9 lead loop |
| P2.15 | Customers and partners matched to client references | 2 | `pass1_relationship` | User confirmed this proposed owner on 2026-10-02; original Appendix A still unavailable |
| P2.16 | Executive speaking, upcoming and past | 2 | `name_searches` | Step 8 person-name searches |
| P2.17 | Podcasts | 2 | `name_searches` | Step 8 person-name searches |
| P2.18 | YouTube | 2 | `name_searches` | Step 8 person-name searches |
| P2.19 | Medium and Substack | 2 | `name_searches` | Step 8 person-name searches |
| P2.20 | Review sites | 2 | `name_searches` | Step 8 checks and explicit None outcome |
| P2.21 | Vendor communities and marketplaces | 2 | `name_searches` | Step 8 checks and explicit None outcome |
| P2.22 | Regulatory and quality events | 2 | `news` **proposed** | Closest explicit source work is step 4 news; confirm Appendix A assignment |
| P2.23 | Financial position against peers | 2 | `baseline` **proposed** | Closest explicit work is step 3 financial baseline; confirm Appendix A assignment |
| P2.24 | Competitor moves and peer KPIs | 2 | `news` **proposed** | Closest explicit source work is step 4 news; confirm Appendix A assignment |
| P2.25 | Major holders and activist activity | 2 | `baseline` **proposed** | Closest explicit work is step 3 financial baseline; confirm Appendix A assignment |
| P2.26 | Events sponsored or exhibited at | 2 | `name_searches` **proposed** | Closest explicit work is step 8 people/company name searches; confirm Appendix A assignment |
| P2.27 | Employee content, GitHub, engineering blogs | 2 | `people` **proposed** | Closest explicit work is step 7 people activity; confirm Appendix A assignment |
| P2.28 | Research papers | 2 | `news` **proposed** | Closest explicit source work is step 4 targeted search; confirm Appendix A assignment |

Every checklist row has exactly one configured owner. The proposed owners are
mechanically total so the graph cannot silently omit rows, but their product meaning
is not claimed as settled. `setup`, `distill_grade`, `build`, `gate`, and `deliver`
own no discovery rows; they govern run setup, grading, output generation, validation,
and delivery respectively.
