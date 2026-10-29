-- Migration 0089: dated employments on stored profiles (People List "Former SI and consulting
-- employers (firm and years)"). person_profiles gains ``employments``: the profile's experience
-- entries as a JSON list of {employer, start, end} (start and end are years or ISO dates as the
-- source gave them; end null = to date). The column is personal data like prior_employers: it is
-- nulled by an erasure, so the erased-profile check and the erasure-only trigger are replaced
-- with versions that include it, and app_rw may update it (erasure only; the trigger enforces
-- that). people.job_level may also hold the levels the reference People List has (senior_director,
-- associate_director) and the default level ``other`` (a title with no level cue): the level check
-- is widened, never narrowed. Expand only: no column is dropped or altered. The person's single People List lane and
-- the Found by details need no column: the lane is stored in ``people.classification_json``
-- (field role_lane) and the sources are ``person_sources`` rows (pass, lane, mention url).
-- Rollback: 0089_people_fields.down.sql.

ALTER TABLE person_profiles ADD COLUMN employments jsonb;
COMMENT ON COLUMN person_profiles.employments IS 'Dated experience entries [{employer, start, end}]; NULL when the source gave none. Personal field: erased with the profile.';

ALTER TABLE person_profiles DROP CONSTRAINT ck_person_profiles_erased;
ALTER TABLE person_profiles ADD CONSTRAINT ck_person_profiles_erased CHECK (erased_at IS NULL OR (name IS NULL
      AND current_title IS NULL AND headline IS NULL AND employer IS NULL
      AND prior_employers IS NULL AND employments IS NULL AND join_date IS NULL
      AND role_start IS NULL AND certifications IS NULL AND location IS NULL
      AND own_description IS NULL AND own_description_sha256 IS NULL AND last_activity_at IS NULL
      AND erasure_reason IS NOT NULL));

GRANT UPDATE (employments) ON person_profiles TO app_rw;
DROP TRIGGER trg_person_profiles_erasure_only ON person_profiles;
CREATE TRIGGER trg_person_profiles_erasure_only BEFORE UPDATE ON person_profiles
   FOR EACH ROW EXECUTE FUNCTION people_erasure_only('name', 'current_title', 'headline',
      'employer', 'prior_employers', 'employments', 'join_date', 'join_precision', 'role_start',
      'role_precision', 'certifications', 'location', 'own_description', 'own_description_sha256',
      'last_activity_at');

ALTER TABLE people DROP CONSTRAINT ck_people_level;
ALTER TABLE people ADD CONSTRAINT ck_people_level CHECK (job_level IS NULL OR job_level IN
   ('c_level', 'evp', 'svp', 'vp', 'senior_director', 'director', 'associate_director', 'head',
    'senior_manager', 'manager', 'other'));
