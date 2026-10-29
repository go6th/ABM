# People rubric (generated from config; do not edit)

Source files: `config/people_scope.yaml`, `config/people_rubric.yaml`, lane lexicon
`config/job_lanes.yaml`. Everything not named by the PRD is `status: proposal` (D-21 to
D-23). Config hash `1ff92a68f1ffe6e1456d96b643f62b65298cf92ebe1bd4649bfa3988d6d33c75`.

## Levels (most senior first)

Order: c_level, evp, svp, vp, senior_director, director, associate_director, head, senior_manager, manager, other. In scope: associate_director, c_level, director, evp, head, senior_director, senior_manager, svp, vp.

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `level.director` | director | director, dir | Director of Field Service Systems | Directory Services Analyst |
| `level.senior_director` | senior_director | senior director, sr director, sr. director, executive director | Sr. Director, Data Platforms | Director, Data Platforms |
| `level.associate_director` | associate_director | associate director, assoc director, assoc. director, assoc dir, assoc. dir | Associate Director, CRM Platforms | Director, CRM Platforms |
| `level.head` | head | head, global lead | Head of Enterprise Applications | Headcount Planning Analyst |
| `level.senior_manager` | senior_manager | senior manager, sr manager, sr. manager, senior it manager, senior program manager, senior product manager, senior project manager, senior engineering manager, senior technical product manager | Senior Manager, CRM Platforms | Manager, CRM Platforms |
| `level.manager` | manager | manager, mgr | Manager, CRM Platforms | Program Lead |
| `level.vp` | vp | vice president, vp | VP, Business Applications | Valve Planner |
| `level.svp` | svp | senior vice president, senior vice, svp | SVP Digital | Vice President Digital |
| `level.evp` | evp | executive vice president, executive vice, evp | EVP Operations | Vice President Operations |
| `level.c_level` | c_level | chief, cio, cto, ceo, cfo | Chief Information Officer | Mischief Analyst |

## Bands and search passes

| Pass | Band | Levels |
|---|---|---|
| 1 | senior | c_level, evp, svp, vp, senior_director, director, associate_director, head |
| 2 | manager | senior_manager |

## Exclusion (beats inclusion)

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `excl.security` | security | security, cyber, cybersecurity, ciso | Director, Cyber Risk | Director, Salesforce Platform |
| `excl.it_compliance` | it_compliance | it compliance | Head of IT Compliance | Head of IT Operations |
| `excl.it_audit` | it_audit | it audit, internal audit | Director, Internal Audit | Director, IT Architecture |
| `excl.privacy` | privacy | privacy, privacy officer | Director, Data Privacy | Director, Data Platform |

## Review

| Rule | Value | Cues | Positive | Negative |
|---|---|---|---|---|
| `review.compliance` | exclusion_ambiguous | compliance | Director, Compliance Systems | Director, CRM Systems |
| `review.audit` | exclusion_ambiguous | audit | Head of Audit Technology | Head of Analytics Technology |

## Priority (from the title alone; first match wins)

| Rule | Priority | Cues | Patterns | Unless the title names |
|---|---|---|---|---|
| `prio.c.override` | C | executive assistant, assistant to, emeritus, retired |  |  |
| `prio.c.non_it` | C | territory, account manager, clinical, marketing manager, brand, scientist, r&d, research, field service, plant, manufacturing, quality engineer, quality engineering, quality assurance, regulatory affairs, medical director, medical affairs, finance, accounting, tax, treasury, legal, counsel, communications, public affairs, government affairs, investor, talent, learning, payroll, benefits, hr business partner, human resources, general manager, vp/gm | sales rep\w*, recruit\w* | salesforce, sfdc, servicenow, sap, veeva, mulesoft, master data, crm, erp, clinical systems, quality systems, regulatory systems |
| `prio.b.integration` | B | m&a, merger, mergers, acquisition, acquisitions, business integration, integration management, divestiture, separation, commercial integration, process integration, systems integration |  |  |
| `prio.a.platform_it` | A | salesforce, sfdc, servicenow, sap, veeva, mulesoft, tableau, erp, crm, mdm, master data, middleware, enterprise integration, solution integration, shared services, gbs, service delivery, e-commerce, ecommerce, cpq, order to cash, supply chain systems, ai, global business solutions, revenue officer, supply chain officer, direct to consumer, analytics, business intelligence, quality systems, clinical systems, regulatory systems, head of it, commercial technology, commercial systems, sales technology, revenue operations, revenue technology, sales enablement, sales operations and effectiveness, sales operations & effectiveness, enterprise architecture, enterprise applications, enterprise data, enterprise ai, enterprise architect | chief (information|digital|technology|data)\w*, (vp|vice president)( of)?,? (it|information|technology|digital|data|analytics), data (engineering|platform|governance|architect)\w*, digital (transformation|solutions|technology|platforms|excellence), (integration|middleware|api) architect\w*, it (solution|enterprise)\w*, architecture |  |

No rule matches: `B` (`prio.b.default`).

## People List lanes (one per person, from the title alone; first match wins)

| Rule | Lane | Cues | Patterns |
|---|---|---|---|
| `lane.salesforce_commercial` | salesforce_commercial | salesforce, sfdc, crm, cpq, e-commerce, ecommerce, digital commerce | commercial (technology|systems|excellence|operations|effectiveness), sales (technology|operations|enablement|effectiveness), revenue (operations|technology), customer (engagement|experience), marketing (technology|automation|operations) |
| `lane.salesforce_integration` | salesforce_integration | mulesoft, middleware, api, enterprise integration, solution integration, technical integration | integration (engineer|architect|platform|developer|lead|engineering)\w* |
| `lane.servicenow_shared` | servicenow_shared | servicenow, itsm, service management, shared services, global business services, gbs, service delivery, workday, hris | hr (technology|systems|service)\w* |
| `lane.veeva_life_sciences` | veeva_life_sciences | veeva, vault, medical affairs systems, regulatory systems, quality systems, clinical systems, promomats, mlr |  |
| `lane.sap_erp` | sap_erp | sap, erp, master data, mdm, order management, order to cash, plm, abap, basis | s/4\w*, supply chain (systems|technology|digital)\w* |
| `lane.analytics_data_ai` | analytics_data_ai | tableau, analytics, insights, visualization, bi, business intelligence, ai, machine learning, artificial intelligence, decision science, data and, data & | data (product|engineering|science|scientist|platform|governance|architect|management|strategy|analytics)\w* |
| `lane.technology_it` | technology_it | digital, information, it, software, cloud, devops, infrastructure, enterprise applications, automation | technolog\w*, architect\w*, applications?, platforms?, cyber\w* |

No rule matches: `business_other`.

## Below the seniority rule

Levels below the rule: manager, other; a title with no level cue is `other`. Such a person stays on the list (Meets seniority rule No) only when the title names a platform of the lane lexicon or one of these technology words: integration, integrator, data, master data, erp, crm, cpq, analytics, bi, business intelligence, middleware, api, cloud, azure, software, system, systems, platform, digital, technology, it, architect, architecture, developer, devops, automation, ai, machine learning, infrastructure, applications, application, hyperion, kinaxis, oracle, workday, abap, fico.

## Former SI and consulting employers

Firms: Accenture, Deloitte, PwC, EY, KPMG, Capgemini, Cognizant, Infosys, TCS, Wipro, HCL, IBM, McKinsey, BCG, Bain, Tech Mahindra, LTIMindtree, Genpact, DXC, Slalom, Persistent Systems, CitiusTech, Virtusa, Hexaware, NTT Data, Mphasis, EPAM, Globant.

## Lanes (shared lexicon)

| Lane | Terms |
|---|---|
| salesforce | Salesforce, MuleSoft, SFDC, Sales Cloud, Service Cloud, Marketing Cloud, Experience Cloud, Salesforce CPQ, Salesforce Field Service, Field Service Lightning, Agentforce, Health Cloud, Commerce Cloud, Salesforce Data Cloud, Einstein, Visualforce, Lightning Web Components, Salesforce Lightning, Salesforce Flow, Mulesoft Anypoint, Anypoint Platform, Anypoint, Mule ESB |
| servicenow | ServiceNow, Service Now, Now Platform, ServiceNow ITSM, ServiceNow ITOM, ServiceNow HRSD, ServiceNow CSM, ServiceNow IRM, ServiceNow SecOps, ServiceNow GRC |
| sap | SAP, Ariba, Concur, S/4HANA, S4HANA, S/4 HANA, SAP S4, SAP ECC, SAP HANA, SAP Ariba, SAP IBP, SAP MDG, SAP Datasphere, SAP Analytics Cloud, SAP EWM, SAP Signavio, SAP SuccessFactors, SuccessFactors, SAP BTP, SAP Fiori, ABAP, SAP BW, SAP Concur, SAP Central Finance |
| tableau_analytics | Tableau, Power BI, Qlik, Looker, MicroStrategy, Tableau Server, Tableau Cloud, Tableau Prep, Tableau Desktop, CRM Analytics, Einstein Analytics, Tableau Pulse, PowerBI, Microsoft Power BI, QlikView, Qlik Sense, Looker Studio |
| veeva | Veeva, Veeva CRM, Vault CRM, Veeva Vault, Vault PromoMats, PromoMats, Vault MedComms, MedComms, Veeva OpenData, Crossix, Vault Clinical, Vault RIM, Vault Quality, Vault QMS, Vault QualityDocs, Vault Safety, eTMF, CTMS, Veeva Network, Veeva Align, Veeva Nitro, Veeva Compass, Vault Submissions |
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
