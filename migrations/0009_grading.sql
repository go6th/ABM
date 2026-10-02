-- Grading engine (expand only). No existing column is dropped or altered.
-- 1. facts: grading columns, write-path idempotency, the two grade rules mirrored in the database,
--    append-only guard, and a deferred "no fact without evidence" check.
-- 2. fact_evidence (new): URL and verbatim supporting sentence per evidence row.
-- 3. Insert-only audit tables: grade_events, claim_drops, lead_verifications, lead_verification_queries.
-- See docs/grading/rules.md and docs/decisions/ADR-0010..0014.

ALTER TABLE facts ADD CONSTRAINT uq_facts_tenant_id_id UNIQUE (tenant_id, id);

ALTER TABLE facts
   ADD COLUMN run_id                uuid,
   ADD COLUMN claim_hash            text,
   ADD COLUMN write_key             text,
   ADD COLUMN claim_kind            text,
   ADD COLUMN value_text            text,
   ADD COLUMN confirming_question   text,
   ADD COLUMN reasoning             text,
   ADD COLUMN flags                 jsonb NOT NULL DEFAULT '[]'::jsonb,
   ADD COLUMN precedence_rule       text,
   ADD COLUMN config_hash           text,
   ADD COLUMN schema_version        integer,
   ADD COLUMN lead_status           text,
   ADD COLUMN may_state_to_prospect boolean NOT NULL DEFAULT false,
   ADD COLUMN superseded_by         uuid,
   ADD CONSTRAINT ck_facts_claim_kind CHECK (claim_kind IN ('sourced', 'synthesis', 'absence')),
   ADD CONSTRAINT ck_facts_lead_status CHECK (lead_status IN ('pending', 'complete', 'upgraded', 'incomplete')),
   ADD CONSTRAINT ck_facts_lead_has_status CHECK (product_grade <> 'lead' OR claim_kind IS NULL OR lead_status IS NOT NULL),
   ADD CONSTRAINT fk_facts_tenant_id_superseded_by_facts
      FOREIGN KEY (tenant_id, superseded_by) REFERENCES facts (tenant_id, id);

-- The rules the code enforces, mirrored here. Added NOT VALID then validated: VALIDATE fails (and the
-- migration stops) if any existing row violates them, which is the "stop and ask" condition.
ALTER TABLE facts ADD CONSTRAINT ck_facts_product_grade_present CHECK (product_grade IS NOT NULL) NOT VALID;
ALTER TABLE facts ADD CONSTRAINT ck_facts_confirmed_needs_primary_read
   CHECK (product_grade <> 'confirmed' OR read_in_primary_this_run) NOT VALID;
ALTER TABLE facts ADD CONSTRAINT ck_facts_only_confirmed_may_be_stated
   CHECK (may_state_to_prospect = (product_grade = 'confirmed')) NOT VALID;
ALTER TABLE facts VALIDATE CONSTRAINT ck_facts_product_grade_present;
ALTER TABLE facts VALIDATE CONSTRAINT ck_facts_confirmed_needs_primary_read;
ALTER TABLE facts VALIDATE CONSTRAINT ck_facts_only_confirmed_may_be_stated;

CREATE UNIQUE INDEX uq_facts_tenant_id_write_key ON facts (tenant_id, write_key) WHERE write_key IS NOT NULL;
CREATE INDEX ix_facts_run_id ON facts (run_id) WHERE run_id IS NOT NULL;

-- Append-only: a later grade is a new superseding row. Only superseded_at and superseded_by may be set,
-- once, from NULL.
CREATE FUNCTION facts_guard_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF (to_jsonb(NEW) - 'superseded_at' - 'superseded_by' - 'status')
      IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_at' - 'superseded_by' - 'status') THEN
      RAISE EXCEPTION 'facts: rows are append-only; write a superseding fact instead'
         USING ERRCODE = 'check_violation';
   END IF;
   IF OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
      RAISE EXCEPTION 'facts: superseded_at is set once' USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER facts_guard_append_only BEFORE UPDATE ON facts
   FOR EACH ROW EXECUTE FUNCTION facts_guard_append_only();

CREATE TABLE fact_evidence (
   id                  uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id           uuid NOT NULL DEFAULT app_tenant(),
   fact_id             uuid NOT NULL,
   evidence_kind       text NOT NULL,
   url                 text,
   supporting_sentence text NOT NULL,
   span_sha256         text,
   source_id           uuid,
   source_ref          text,
   call_ref            text,
   origin              text,
   source_tier         text,
   lineage_group       text,
   is_aggregator       boolean NOT NULL DEFAULT false,
   date_kind           text,
   evidence_date       timestamptz,
   provider_code       text,
   created_at          timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_fact_evidence PRIMARY KEY (id),
   CONSTRAINT fk_fact_evidence_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_fact_evidence_tenant_id_fact_id_facts FOREIGN KEY (tenant_id, fact_id) REFERENCES facts (tenant_id, id),
   CONSTRAINT ck_fact_evidence_kind CHECK (evidence_kind IN ('source', 'inherited', 'search_proof')),
   CONSTRAINT ck_fact_evidence_sentence_length CHECK (char_length(supporting_sentence) BETWEEN 1 AND 2000),
   CONSTRAINT ck_fact_evidence_url_http CHECK (url IS NULL OR url ~* '^https?://\S+$'),
   CONSTRAINT ck_fact_evidence_span_sha256 CHECK (span_sha256 IS NULL OR span_sha256 ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_fact_evidence_origin CHECK (origin IN ('full_page_read', 'cached_copy', 'search_snippet', 'feed_item', 'broker_record')),
   CONSTRAINT ck_fact_evidence_tier CHECK (source_tier IN ('T1', 'T2', 'T3', 'T4')),
   CONSTRAINT ck_fact_evidence_date_kind CHECK (date_kind IN ('observed', 'published', 'unknown')),
   CONSTRAINT ck_fact_evidence_provider_code CHECK (provider_code ~ '^p_[a-z]+$'),
   -- a source or inherited row is fully traceable; a search_proof row names the call it proves
   CONSTRAINT ck_fact_evidence_traceable CHECK (
      evidence_kind = 'search_proof'
      OR (url IS NOT NULL AND span_sha256 IS NOT NULL AND source_tier IS NOT NULL AND evidence_date IS NOT NULL)),
   CONSTRAINT ck_fact_evidence_search_proof_call CHECK (evidence_kind <> 'search_proof' OR call_ref IS NOT NULL)
);
CREATE INDEX ix_fact_evidence_fact_id ON fact_evidence (fact_id);
ALTER TABLE fact_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON fact_evidence TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
-- The public renderer reads evidence of public facts only (two-output rule).
CREATE POLICY public_only ON fact_evidence FOR SELECT TO public_reader
   USING (tenant_id = (SELECT app_tenant())
          AND EXISTS (SELECT 1 FROM facts f WHERE f.id = fact_evidence.fact_id AND f.visibility = 'public'));
GRANT SELECT, INSERT ON fact_evidence TO app_rw;          -- insert-only
GRANT SELECT ON fact_evidence TO public_reader;

-- No fact without at least one evidence row, checked at commit. Runs as the inserting role, so
-- under RLS it sees exactly the evidence of the same tenant.
CREATE FUNCTION facts_require_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF NOT EXISTS (SELECT 1 FROM fact_evidence e WHERE e.fact_id = NEW.id AND e.tenant_id = NEW.tenant_id) THEN
      RAISE EXCEPTION 'facts: fact % has no evidence row at commit', NEW.id USING ERRCODE = 'check_violation';
   END IF;
   RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER facts_require_evidence AFTER INSERT ON facts
   DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION facts_require_evidence();

CREATE TABLE grade_events (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   fact_id       uuid NOT NULL,
   run_id        uuid,
   from_product  text,
   to_product    text NOT NULL,
   from_engine   text,
   to_engine     text,
   actor         text NOT NULL,
   reason_code   text NOT NULL,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_grade_events PRIMARY KEY (id),
   CONSTRAINT fk_grade_events_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_grade_events_tenant_id_fact_id_facts FOREIGN KEY (tenant_id, fact_id) REFERENCES facts (tenant_id, id),
   CONSTRAINT ck_grade_events_to_product CHECK (to_product IN ('confirmed', 'probable', 'hypothesis', 'lead', 'internal'))
);
ALTER TABLE grade_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE grade_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON grade_events TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON grade_events TO app_rw;
GRANT USAGE ON SEQUENCE grade_events_id_seq TO app_rw;

CREATE TABLE claim_drops (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   reason_code   text NOT NULL,
   attribute_key text,
   claim_hash    text NOT NULL,
   excerpt       text NOT NULL,
   hostile_url   text,
   created_at    timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_claim_drops PRIMARY KEY (id),
   CONSTRAINT uq_claim_drops_tenant_id_run_id_claim_hash_reason_code UNIQUE (tenant_id, run_id, claim_hash, reason_code),
   CONSTRAINT fk_claim_drops_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_claim_drops_excerpt_length CHECK (char_length(excerpt) <= 200)
);
ALTER TABLE claim_drops ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_drops FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON claim_drops TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON claim_drops TO app_rw;
GRANT USAGE ON SEQUENCE claim_drops_id_seq TO app_rw;

CREATE TABLE lead_verifications (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   run_id          uuid NOT NULL,
   lead_key        text NOT NULL,
   status          text NOT NULL,
   reason          text,
   exit_reason     text,
   accepted_count  integer NOT NULL,
   categories      text[] NOT NULL,
   repasses_used   integer NOT NULL DEFAULT 0,
   created_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_lead_verifications PRIMARY KEY (id),
   CONSTRAINT uq_lead_verifications_tenant_id_id UNIQUE (tenant_id, id),
   CONSTRAINT fk_lead_verifications_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_lead_verifications_status CHECK (status IN ('pending', 'complete', 'upgraded', 'incomplete')),
   -- the Lead rule mirrored (numbers from grading.yaml: 3 queries, 2 tool categories)
   CONSTRAINT ck_lead_verifications_complete_rule
      CHECK (status <> 'complete' OR (accepted_count >= 3 AND cardinality(categories) >= 2))
);
ALTER TABLE lead_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_verifications FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON lead_verifications TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON lead_verifications TO app_rw;

CREATE TABLE lead_verification_queries (
   id                    bigserial NOT NULL,
   tenant_id             uuid NOT NULL DEFAULT app_tenant(),
   lead_verification_id  uuid NOT NULL,
   seq                   integer NOT NULL,
   query_text            text NOT NULL,
   tool_category         text NOT NULL,
   call_ref              text NOT NULL,
   accepted              boolean NOT NULL,
   rejection_reason      text,
   created_at            timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_lead_verification_queries PRIMARY KEY (id),
   CONSTRAINT fk_lead_verification_queries_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_lead_verification_queries_tenant_id_lead_verification_id
      FOREIGN KEY (tenant_id, lead_verification_id) REFERENCES lead_verifications (tenant_id, id),
   CONSTRAINT ck_lead_verification_queries_reason CHECK (accepted = (rejection_reason IS NULL))
);
ALTER TABLE lead_verification_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_verification_queries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON lead_verification_queries TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON lead_verification_queries TO app_rw;
GRANT USAGE ON SEQUENCE lead_verification_queries_id_seq TO app_rw;
