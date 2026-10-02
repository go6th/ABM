-- Phase 1 data-layer v1. Expand-only: do not drop or rewrite existing records.
-- Provider names never belong in object keys or client-safe records. The provider_code
-- in raw_payloads is internal operational metadata.

ALTER TABLE accounts
    ADD CONSTRAINT accounts_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE account_runs
    ADD CONSTRAINT account_runs_tenant_id_id_key UNIQUE (tenant_id, id),
    ADD CONSTRAINT account_runs_tenant_account_fk
        FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    ADD COLUMN checklist_version text NOT NULL DEFAULT '1.1.0',
    ADD COLUMN expected_coverage_rows integer NOT NULL DEFAULT 33
        CHECK (expected_coverage_rows > 0),
    ADD COLUMN worker_lease_token uuid,
    ADD COLUMN worker_lease_expires_at timestamptz,
    ADD CONSTRAINT account_runs_worker_lease_pair
        CHECK ((worker_lease_token IS NULL) = (worker_lease_expires_at IS NULL));
ALTER TABLE facts
    ADD CONSTRAINT facts_tenant_account_id_key UNIQUE (tenant_id, account_id, id),
    ADD CONSTRAINT facts_tenant_account_fk
        FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id);

CREATE TABLE source_code_counters (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    next_value bigint NOT NULL DEFAULT 1 CHECK (next_value > 0),
    PRIMARY KEY (tenant_id, account_id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id)
);

CREATE TABLE sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    source_code text NOT NULL CHECK (source_code ~ '^S-[0-9]{4,}$'),
    url text NOT NULL,
    url_hash char(64) NOT NULL CHECK (url_hash ~ '^[0-9a-f]{64}$'),
    domain text NOT NULL,
    tier text NOT NULL CHECK (tier IN ('T1', 'T2', 'T3', 'T4')),
    lineage_group text NOT NULL,
    content_hash char(64) CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, id),
    UNIQUE (tenant_id, account_id, url_hash),
    UNIQUE (tenant_id, account_id, source_code),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id)
);

CREATE TABLE fact_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    fact_id uuid NOT NULL,
    source_id uuid NOT NULL,
    source_code text NOT NULL,
    lineage_group text NOT NULL,
    quote_text text NOT NULL,
    retrieved_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, fact_id, source_id),
    FOREIGN KEY (tenant_id, account_id, fact_id)
        REFERENCES facts (tenant_id, account_id, id),
    FOREIGN KEY (tenant_id, account_id, source_id)
        REFERENCES sources (tenant_id, account_id, id)
);

CREATE TABLE coverage_rows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    run_id uuid NOT NULL,
    checklist_version text NOT NULL,
    row_key text NOT NULL CHECK (row_key ~ '^P[12]\.[0-9]{2}$'),
    pass smallint NOT NULL CHECK (pass IN (1, 2)),
    description text NOT NULL,
    priority text NOT NULL CHECK (priority IN ('High', 'Medium', 'Optional')),
    status text CHECK (status IN ('Done', 'Partial', 'Not done', 'Blocked', 'None')),
    queries jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(queries) = 'array'),
    tool_categories jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(tool_categories) = 'array'),
    result_count integer CHECK (result_count IS NULL OR result_count >= 0),
    blocked_reason text,
    last_checked_at timestamptz,
    UNIQUE (tenant_id, run_id, row_key),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
    CHECK (status IS DISTINCT FROM 'Blocked' OR nullif(trim(blocked_reason), '') IS NOT NULL)
);

CREATE TABLE people (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    name text NOT NULL,
    normalized_name text NOT NULL,
    profile_url text,
    profile_url_hash char(64),
    title text NOT NULL,
    level text,
    lane text,
    priority char(1) CHECK (priority IN ('A', 'B', 'C')),
    role_started_at date,
    tenure_months integer CHECK (tenure_months IS NULL OR tenure_months >= 0),
    excluded boolean NOT NULL DEFAULT false,
    exclusion_reason text,
    merged_into uuid,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, account_id, merged_into)
        REFERENCES people (tenant_id, account_id, id),
    CHECK (NOT excluded OR nullif(trim(exclusion_reason), '') IS NOT NULL),
    CHECK (merged_into IS NULL OR merged_into <> id)
);
CREATE UNIQUE INDEX people_profile_url_dedupe
    ON people (tenant_id, account_id, profile_url_hash)
    WHERE profile_url_hash IS NOT NULL;
CREATE UNIQUE INDEX people_name_dedupe_without_profile
    ON people (tenant_id, account_id, normalized_name)
    WHERE profile_url_hash IS NULL;

CREATE TABLE job_postings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    posting_url text NOT NULL,
    posting_url_hash char(64) NOT NULL CHECK (posting_url_hash ~ '^[0-9a-f]{64}$'),
    title text NOT NULL,
    posting_class text NOT NULL
        CHECK (posting_class IN ('build', 'admin_owner', 'working_tool', 'user')),
    platform_sentence text,
    full_text text,
    full_text_read boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, posting_url_hash),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    CHECK (NOT full_text_read OR nullif(trim(full_text), '') IS NOT NULL)
);

CREATE TABLE tech_detections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    technology text NOT NULL,
    evidence_url text NOT NULL,
    evidence_hash char(64) NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    evidence_text text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    cross_check_result text NOT NULL DEFAULT 'unverified'
        CHECK (cross_check_result IN ('unverified', 'corroborated', 'contradicted')),
    cross_checked_at timestamptz,
    corroborating_posting_ids uuid[] NOT NULL DEFAULT '{}',
    product_grade text NOT NULL DEFAULT 'lead'
        CHECK (product_grade IN ('confirmed', 'probable', 'hypothesis', 'lead')),
    UNIQUE (tenant_id, account_id, technology, evidence_hash),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    CHECK (
        cross_check_result = 'corroborated'
        OR product_grade = 'lead'
    )
);

CREATE TABLE raw_payloads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    account_id uuid NOT NULL,
    run_id uuid NOT NULL,
    args_hash char(64) NOT NULL CHECK (args_hash ~ '^[0-9a-f]{64}$'),
    object_key text NOT NULL,
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    provider_code text NOT NULL CHECK (provider_code ~ '^p_[a-z0-9_]+$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, run_id, args_hash),
    UNIQUE (object_key),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
    CHECK (object_key !~* '(bitscale|sumble|predictleads|firecrawl|parallel)')
);

CREATE TABLE tool_call_intents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    run_id uuid NOT NULL,
    tool text NOT NULL,
    args_hash char(64) NOT NULL CHECK (args_hash ~ '^[0-9a-f]{64}$'),
    intent_token uuid NOT NULL,
    status text NOT NULL DEFAULT 'IN_FLIGHT'
        CHECK (status IN ('IN_FLIGHT', 'COMPLETED', 'UNKNOWN')),
    started_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    UNIQUE (tenant_id, run_id, args_hash),
    FOREIGN KEY (tenant_id, run_id) REFERENCES account_runs (tenant_id, id),
    CHECK ((status = 'IN_FLIGHT') = (finished_at IS NULL))
);

CREATE OR REPLACE FUNCTION validate_tool_call_intent_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['status', 'finished_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status', 'finished_at']) THEN
        RAISE EXCEPTION 'tool-call intent identity is immutable';
    END IF;
    IF OLD.status <> 'IN_FLIGHT'
       OR NEW.status NOT IN ('COMPLETED', 'UNKNOWN')
       OR NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'tool-call intent has an invalid state transition';
    END IF;
    IF NEW.status = 'COMPLETED' AND NOT EXISTS (
        SELECT 1 FROM tool_calls call
        WHERE call.tenant_id = NEW.tenant_id
          AND call.run_id = NEW.run_id
          AND call.args_hash = NEW.args_hash
    ) THEN
        RAISE EXCEPTION 'completed tool-call intent requires a persisted response';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER tool_call_intent_state_guard
BEFORE UPDATE ON tool_call_intents
FOR EACH ROW EXECUTE FUNCTION validate_tool_call_intent_transition();

CREATE OR REPLACE FUNCTION protect_source_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source identifiers are retained and never reused';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['content_hash', 'last_seen_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['content_hash', 'last_seen_at']) THEN
        RAISE EXCEPTION 'source identity and allocation fields are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER sources_identity_immutable
BEFORE UPDATE OR DELETE ON sources
FOR EACH ROW EXECUTE FUNCTION protect_source_identity();

CREATE OR REPLACE FUNCTION protect_source_counter()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source code counters are retained to prevent code reuse';
    END IF;
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.next_value < OLD.next_value THEN
        RAISE EXCEPTION 'source code counters cannot be reassigned or decremented';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_counter_monotonic
BEFORE UPDATE OR DELETE ON source_code_counters
FOR EACH ROW EXECUTE FUNCTION protect_source_counter();

-- Protect existing facts as append-only values. Only the one-time superseded_at
-- transition is allowed; facts and evidence cannot be deleted by the runtime role.
CREATE OR REPLACE FUNCTION prevent_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'facts are append-only';
    END IF;
    IF (to_jsonb(NEW) - 'superseded_at') IS DISTINCT FROM
       (to_jsonb(OLD) - 'superseded_at') THEN
        RAISE EXCEPTION 'fact values are append-only';
    END IF;
    IF OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
        RAISE EXCEPTION 'a superseded fact cannot be changed again';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER facts_append_only
BEFORE UPDATE OR DELETE ON facts
FOR EACH ROW EXECUTE FUNCTION prevent_fact_mutation();

CREATE OR REPLACE FUNCTION require_fact_evidence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM fact_evidence e
        WHERE e.tenant_id = NEW.tenant_id
          AND e.account_id = NEW.account_id
          AND e.fact_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'fact % requires at least one evidence row', NEW.id;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER fact_requires_evidence
AFTER INSERT ON facts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_fact_evidence();

CREATE OR REPLACE FUNCTION prevent_person_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'people rows are retained; mark a merge or exclusion instead';
END;
$$;
CREATE TRIGGER people_never_delete
BEFORE DELETE ON people
FOR EACH ROW EXECUTE FUNCTION prevent_person_delete();

CREATE OR REPLACE FUNCTION prevent_person_merge_chains()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.merged_into IS DISTINCT FROM OLD.merged_into AND NEW.merged_into IS NOT NULL THEN
        IF NEW.merged_into = NEW.id OR EXISTS (
            SELECT 1
            FROM people target
            WHERE target.tenant_id = NEW.tenant_id
              AND target.account_id = NEW.account_id
              AND target.id = NEW.merged_into
              AND target.merged_into IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'people can only merge into a distinct, unmerged target';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER people_merge_target_guard
BEFORE UPDATE OF merged_into ON people
FOR EACH ROW EXECUTE FUNCTION prevent_person_merge_chains();

CREATE OR REPLACE FUNCTION require_high_coverage_before_completion()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    row_total integer;
BEGIN
    IF NEW.status = 'COMPLETED' AND OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT count(*) INTO row_total
        FROM coverage_rows
        WHERE tenant_id = NEW.tenant_id AND run_id = NEW.id
          AND checklist_version = NEW.checklist_version;

        IF row_total <> NEW.expected_coverage_rows THEN
            RAISE EXCEPTION 'run % has % coverage rows; expected % for checklist %',
                NEW.id, row_total, NEW.expected_coverage_rows, NEW.checklist_version;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM coverage_rows c
            WHERE c.tenant_id = NEW.tenant_id
              AND c.run_id = NEW.id
              AND c.checklist_version = NEW.checklist_version
              AND c.priority = 'High'
              AND (
                  c.status IS NULL
                  OR (
                      c.status = 'Blocked'
                      AND nullif(trim(c.blocked_reason), '') IS NULL
                  )
                  OR (
                      c.status <> 'Blocked'
                      AND (
                          jsonb_array_length(c.queries) = 0
                          OR jsonb_array_length(c.tool_categories) = 0
                          OR c.result_count IS NULL
                      )
                  )
              )
        ) THEN
            RAISE EXCEPTION 'run % has High priority coverage without a status and proof', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER account_runs_require_coverage
BEFORE UPDATE OF status ON account_runs
FOR EACH ROW EXECUTE FUNCTION require_high_coverage_before_completion();

-- All tenant-owned data-layer tables receive an explicit app_rw role policy.
-- lint:covers source_code_counters, sources, fact_evidence, coverage_rows, people, job_postings, tech_detections, raw_payloads, tool_call_intents
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'source_code_counters', 'sources', 'fact_evidence', 'coverage_rows',
        'people', 'job_postings', 'tech_detections', 'raw_payloads', 'tool_call_intents'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY tenant_isolation ON %I TO app_rw
            USING (tenant_id = (SELECT app_tenant()))
            WITH CHECK (tenant_id = (SELECT app_tenant()))$p$, t);
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE ON source_code_counters TO app_rw;
GRANT SELECT, INSERT, UPDATE ON sources TO app_rw;
GRANT SELECT, INSERT ON fact_evidence TO app_rw;
GRANT SELECT, INSERT, UPDATE ON coverage_rows TO app_rw;
GRANT SELECT, INSERT, UPDATE ON people, job_postings, tech_detections TO app_rw;
GRANT SELECT, INSERT ON raw_payloads TO app_rw;
GRANT SELECT, INSERT ON tool_call_intents TO app_rw;
GRANT UPDATE (status, finished_at) ON tool_call_intents TO app_rw;
GRANT UPDATE (checklist_version, expected_coverage_rows, worker_lease_token,
    worker_lease_expires_at) ON account_runs TO app_rw;
REVOKE DELETE ON facts, source_code_counters, sources, fact_evidence, coverage_rows,
    people, job_postings, tech_detections, raw_payloads, tool_call_intents FROM app_rw;
REVOKE UPDATE, DELETE ON fact_evidence, raw_payloads FROM app_rw;
