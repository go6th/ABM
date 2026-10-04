-- Rollback of 0014_people_pipeline.sql.
DROP TABLE people_pipeline_runs;
DROP TABLE person_events;
DROP TABLE person_post_pulls;
DROP TABLE person_posts;
DROP TABLE person_profiles;
DROP TABLE person_sources;
DROP TABLE person_search_pages;
DROP FUNCTION people_erasure_only();
REVOKE UPDATE ON people FROM app_rw;
DROP TRIGGER trg_people_guard ON people;
DROP FUNCTION people_guard();
DROP INDEX ix_people_merged_into;
DROP INDEX ix_people_account_id_profile_url_canonical;
DROP INDEX uq_people_run_canonical;
DROP INDEX uq_people_run_person_key;
ALTER TABLE people
   DROP CONSTRAINT ck_people_erased,
   DROP CONSTRAINT ck_people_fields_dropped,
   DROP CONSTRAINT ck_people_profile_state,
   DROP CONSTRAINT ck_people_origin,
   DROP CONSTRAINT ck_people_not_self,
   DROP CONSTRAINT ck_people_merged,
   DROP CONSTRAINT ck_people_priority_in_scope,
   DROP CONSTRAINT ck_people_priority,
   DROP CONSTRAINT ck_people_level,
   DROP CONSTRAINT ck_people_reason,
   DROP CONSTRAINT ck_people_excluded_status,
   DROP CONSTRAINT ck_people_in_scope_url,
   DROP CONSTRAINT ck_people_scope_status,
   DROP COLUMN erasure_reason, DROP COLUMN erased_at, DROP COLUMN fields_dropped,
   DROP COLUMN activity_unfiltered, DROP COLUMN posts_requested, DROP COLUMN profile_failure,
   DROP COLUMN profile_state, DROP COLUMN internal_only, DROP COLUMN possible_duplicate,
   DROP COLUMN merged_into, DROP COLUMN priority, DROP COLUMN lanes, DROP COLUMN classification_json,
   DROP COLUMN rule_id, DROP COLUMN scope_reason, DROP COLUMN scope_status,
   DROP COLUMN profile_url_canonical, DROP COLUMN employer_domain, DROP COLUMN location,
   DROP COLUMN headline, DROP COLUMN origin, DROP COLUMN source_record_id, DROP COLUMN person_key;
