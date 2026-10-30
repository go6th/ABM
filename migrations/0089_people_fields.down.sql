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
-- Rows stored with the wider levels are mapped back to a level the old check allows BEFORE the
-- old check is added, validated (security review 2026-10-29, F8: a NOT VALID check is still
-- enforced on every row written afterwards, so any later UPDATE of such a person, the erasure
-- tombstone included, would have failed). The mapping is the closest old level; ``other`` (a
-- title with no level cue) has no old equivalent and becomes NULL, which the old check allows.
-- Nothing is lost: the person's full classification, with the level the rubric gave, stays in
-- ``classification_json`` (field ``level``), which this script does not touch.
UPDATE people SET job_level = CASE job_level
      WHEN 'senior_director' THEN 'director'
      WHEN 'associate_director' THEN 'director'
      ELSE NULL END
   WHERE job_level IN ('senior_director', 'associate_director', 'other');
ALTER TABLE people DROP CONSTRAINT IF EXISTS ck_people_level;
ALTER TABLE people ADD CONSTRAINT ck_people_level CHECK (job_level IS NULL OR job_level IN
   ('c_level', 'evp', 'svp', 'vp', 'head', 'director', 'senior_manager', 'manager'));
