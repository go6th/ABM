# People rubric (generated from config; do not edit)

Source files: `config/people_scope.yaml`, `config/people_rubric.yaml`, lane lexicon
`config/job_lanes.yaml`. Everything not named by the PRD is `status: proposal` (D-21 to
D-23). Config hash `26e4659fd8967d77a4fe76c9af7e2c9ae5ab6f7d76300693c2b829d97cd57bc1`.

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
| `excl.privacy` | privacy | privacy | Director, Data Privacy | Director, Data Platform |

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
| salesforce | Salesforce, MuleSoft, SFDC, Sales Cloud, Service Cloud, Marketing Cloud, Experience Cloud, Salesforce CPQ, Salesforce Field Service, Field Service Lightning, Agentforce, Salesforce Data Cloud, Einstein, Apex, Visualforce, Lightning Web Components, Salesforce Lightning, Salesforce Flow, Mulesoft Anypoint, Anypoint Platform, Anypoint, Mule ESB |
| servicenow | ServiceNow, Service Now, Now Platform, ServiceNow ITSM, ServiceNow ITOM, ServiceNow HRSD, ServiceNow CSM, ServiceNow IRM, ServiceNow SecOps, ServiceNow GRC |
| sap | SAP, S/4HANA, S4HANA, S/4 HANA, SAP S4, SAP ECC, SAP HANA, SAP Ariba, SAP IBP, SAP MDG, SAP Datasphere, SAP Analytics Cloud, SAP EWM, SAP Signavio, SAP SuccessFactors, SuccessFactors, SAP BTP, SAP Fiori, ABAP, SAP BW, SAP Concur, SAP Central Finance |
| tableau_analytics | Tableau, Power BI, Qlik, Looker, MicroStrategy, Tableau Server, Tableau Cloud, Tableau Prep, Tableau Desktop, CRM Analytics, Einstein Analytics, Tableau Pulse, PowerBI, Microsoft Power BI, QlikView, Qlik Sense, Looker Studio |
| veeva | Veeva, Veeva CRM, Vault CRM, Veeva Vault, Vault PromoMats, PromoMats, Vault MedComms, MedComms, Veeva OpenData, Crossix, Vault Clinical, Vault RIM, Vault Quality, Vault QMS, Vault QualityDocs, Vault Safety, Veeva Network, Veeva Align, Veeva Nitro, Veeva Compass, Vault Submissions |
| data_ai | Snowflake, Microsoft Fabric, Azure Synapse, Azure Data Factory, BigQuery, Redshift, Informatica, dbt, Apache Spark, Kafka, Airflow, Palantir, AWS, Azure, Google Cloud, Azure OpenAI, OpenAI, SageMaker, Vertex AI, MLflow, LangChain, Generative AI, Fabric Lakehouse, OneLake, Synapse Analytics, Azure Synapse Analytics, Data Factory, Google BigQuery, Amazon Redshift, IICS, PowerCenter, Informatica Cloud, dbt Cloud, dbt Core, PySpark, Apache Kafka, Confluent, Apache Airflow, Palantir Foundry, Foundry AIP, Amazon Web Services, Microsoft Azure, Google Cloud Platform, GCP, Azure OpenAI Service, ChatGPT Enterprise, GPT-4, Amazon SageMaker, AWS Bedrock, Amazon Bedrock, Google Vertex, Gemini API, LangGraph, LlamaIndex, GenAI, Gen AI, Large Language Models, LLMs, LLM, Agentic AI |

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
