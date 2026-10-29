-- Undo migration 0089: dated employments on stored profiles.
DROP TRIGGER IF EXISTS trg_person_profiles_erasure_only ON person_profiles;
CREATE TRIGGER trg_person_profiles_erasure_only BEFORE UPDATE ON person_profiles
   FOR EACH ROW EXECUTE FUNCTION people_erasure_only('name', 'current_title', 'headline',
      'employer', 'prior_employers', 'join_date', 'join_precision', 'role_start', 'role_precision',
      'certifications', 'location', 'own_description', 'own_description_sha256',
      'last_activity_at');
REVOKE UPDATE (employments) ON person_profiles FROM app_rw;
ALTER TABLE person_profiles DROP CONSTRAINT IF EXISTS ck_person_profiles_erased;
ALTER TABLE person_profiles ADD CONSTRAINT ck_person_profiles_erased CHECK (erased_at IS NULL OR (name IS NULL
      AND current_title IS NULL AND headline IS NULL AND employer IS NULL
      AND prior_employers IS NULL AND join_date IS NULL AND role_start IS NULL
      AND certifications IS NULL AND location IS NULL AND own_description IS NULL
      AND own_description_sha256 IS NULL AND last_activity_at IS NULL
      AND erasure_reason IS NOT NULL));
ALTER TABLE person_profiles DROP COLUMN IF EXISTS employments;
-- rows stored with the wider levels stay (nothing is deleted): the old check is added NOT VALID
-- so it applies to new and changed rows only
ALTER TABLE people DROP CONSTRAINT IF EXISTS ck_people_level;
ALTER TABLE people ADD CONSTRAINT ck_people_level CHECK (job_level IS NULL OR job_level IN
   ('c_level', 'evp', 'svp', 'vp', 'head', 'director', 'senior_manager', 'manager')) NOT VALID;
