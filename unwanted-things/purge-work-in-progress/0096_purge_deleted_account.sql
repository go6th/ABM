-- Migration 0096 (2026-10-30): purge ONE account the user already deleted.
--
-- Deleting an account in the app only MARKS the row (migration 0087: deleted_at, deleted_by);
-- every run, fact, source, person and file of it stays. This migration adds the one narrow,
-- operator-run way to erase the CLIENT CONTENT of such an account for good, without touching the
-- money ledgers or the audit log:
--
--   purge_candidates(domain)                    -> (tenant, account, deleted_at) of every account
--                                                  with that exact domain, across tenants
--   purge_deleted_account(tenant, account, execute, actor, allow_shared) -> jsonb summary
--
-- purge_deleted_account refuses unless the account exists in that tenant AND is deleted AND no
-- research of it is open. With execute = false it deletes and changes nothing and returns, per
-- table, the rows it would erase. With execute = true it runs inside the caller's one statement
-- (one transaction, all or nothing): it erases the account's rows table by table in foreign-key
-- order, scrubs the content columns of the rows the ledgers need to keep (runs, research jobs,
-- tool calls, model calls), turns the account row into a TOMBSTONE (unique `deleted-<8 hex>.invalid`
-- domain, every name, address and setting cleared, deleted_at kept) so adding the domain again
-- makes a clean new account, and writes one audit_events row (identifiers only; the audit table
-- has no detail column) and one content-free tombstones row that carries the per-table counts.
-- What is erased, scrubbed and kept is classified in config/deletion.yaml `account_purge`; a test
-- ties that list, this function and the database catalog together.
--
-- Rights. The function belongs to account_purger (NOLOGIN, NOBYPASSRLS, member of app_rw so the
-- tenant and owner policies apply to it unchanged; the same pattern as tenant_eraser, migration
-- 0020). It sets the tenant and the system scope for its own transaction, because it works on one
-- tenant named by the caller, and puts the previous values back. account_purger holds DELETE on
-- exactly the tables listed in the plan and UPDATE on exactly the content columns it scrubs; no
-- ledger, billing or audit table is deletable. EXECUTE goes to account_purge_runner only (the
-- migration user is made a member): the runtime role app_rw cannot call it, so no application
-- path reaches it. purge_candidates is owned by account_purge_lookup, which can read four columns
-- of accounts across tenants and nothing else.
-- Two guards change with it: sources_guard_identity lets account_purger delete the sources of a
-- deleted account (the rest of the world still cannot), and accounts_tombstone_guard keeps a
-- purged tombstone from being brought back by a late "add account".
-- Rollback: 0096_purge_deleted_account.down.sql (the roles stay: they are cluster-global).

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_purger') THEN
    CREATE ROLE account_purger NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_purge_lookup') THEN
    CREATE ROLE account_purge_lookup NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_purge_runner') THEN
    CREATE ROLE account_purge_runner NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
GRANT app_rw TO account_purger;
GRANT account_purge_runner TO CURRENT_USER;

-- DELETE on exactly the tables of config/deletion.yaml account_purge.erase
GRANT DELETE ON
   fact_evidence, grade_events, verifier_findings, claim_drops, report_sections, deliverables,
   job_posting_platform_sentences, job_posting_classifications, job_posting_observations,
   job_postings, tech_detections, tech_stack_items, link_archive, chunks, abm_distill_outputs,
   abm_sense_checks, alert_feedback, alert_outbox, alerts, chat_turns, chat_threads,
   connector_items, coverage_row_events, coverage_rows, crm_write_events, crm_writes,
   crm_mappings, document_deletions, document_parts, document_passages, document_redactions,
   document_table_rows, document_tables, download_jobs, export_verifications, export_packages,
   fact_reevaluations, intake_drops, internal_deliverables, job_pipeline_passes,
   job_pipeline_runs, lead_verification_queries, lead_verifications, leak_checks, link_checks,
   missing_observations, monitor_runs, monitor_configs, notification_deliveries,
   notification_outbox, notifications, people_pipeline_runs, person_events, person_post_pulls,
   person_search_pages, prepulled_files, prepulled_intent_events, prepulled_intents,
   pull_manifests, raw_files, refresh_row_plans, review_samples, run_coverage_meta,
   run_documents, documents, run_feedback, run_gate_results, signal_event_evidence,
   signal_event_links, signal_history, view_materializations, view_rows, work_unit_events,
   work_units, model_reply_log, alert_suppressions, signal_events, delta_items, deltas, facts,
   refresh_runs, refresh_slots, checkpoint_writes, checkpoint_blobs, checkpoints, sources,
   person_sources, person_posts, person_profiles, people, account_source_counters,
   account_bundles, tenant_rule_accounts
   TO account_purger;
-- UPDATE on exactly the content columns the purge scrubs where app_rw has no such grant
GRANT UPDATE (response, response_ref) ON tool_calls TO account_purger;
GRANT UPDATE (output_json) ON llm_calls TO account_purger;
GRANT UPDATE (run_input, stop_detail) ON account_runs TO account_purger;
GRANT UPDATE (error) ON runs TO account_purger;
GRANT UPDATE (goal, topic, failure_reason) ON research_jobs TO account_purger;

-- The lookup: four columns of accounts across tenants, read only.
GRANT SELECT (id, tenant_id, domain, deleted_at) ON accounts TO account_purge_lookup;
DROP POLICY IF EXISTS purge_lookup_read ON accounts;
CREATE POLICY purge_lookup_read ON accounts FOR SELECT TO account_purge_lookup USING (true);

-- A purged account stays purged: a tombstone domain cannot be un-deleted by a late add_account.
CREATE OR REPLACE FUNCTION accounts_tombstone_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
   IF NEW.deleted_at IS NULL AND OLD.domain ~ '^deleted-[0-9a-f]{8,32}\.invalid$' THEN
      RAISE EXCEPTION 'accounts: a purged account cannot be brought back; add the domain again'
         USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS accounts_tombstone_guard ON accounts;
CREATE TRIGGER accounts_tombstone_guard BEFORE UPDATE OF deleted_at ON accounts
   FOR EACH ROW EXECUTE FUNCTION accounts_tombstone_guard();

-- Sources are never deleted (stable source codes, migration 0005) except by the purge of an
-- account that is already deleted: only account_purger, only while accounts.deleted_at is set.
CREATE OR REPLACE FUNCTION sources_guard_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
   IF TG_OP = 'DELETE' THEN
      IF current_user = 'account_purger' AND EXISTS (
            SELECT 1 FROM accounts a WHERE a.id = OLD.account_id AND a.tenant_id = OLD.tenant_id
               AND a.deleted_at IS NOT NULL) THEN
         RETURN OLD;
      END IF;
      RAISE EXCEPTION 'sources: rows are never deleted (source %)', OLD.source_code
         USING ERRCODE = 'restrict_violation';
   END IF;
   IF NEW.source_code <> OLD.source_code OR NEW.url_hash <> OLD.url_hash
      OR NEW.url_normalized <> OLD.url_normalized OR NEW.account_id <> OLD.account_id
      OR NEW.tenant_id <> OLD.tenant_id
      OR NEW.first_seen_run_id IS DISTINCT FROM OLD.first_seen_run_id
      OR NEW.first_seen_account_run_id IS DISTINCT FROM OLD.first_seen_account_run_id THEN
      RAISE EXCEPTION 'sources: source identity and first-seen references are immutable (source %)',
         OLD.source_code USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;

-- purge_candidates(): every account with this exact domain, in every tenant, deleted or not.
-- reviewed: SECURITY DEFINER, owner account_purge_lookup (NOLOGIN, NOBYPASSRLS; four read-only
-- column grants on accounts and one SELECT policy). Returns identifiers and the deletion time only.
CREATE OR REPLACE FUNCTION purge_candidates(p_domain text)
   RETURNS TABLE (tenant_id uuid, account_id uuid, deleted_at timestamptz)
   LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
#variable_conflict use_column
BEGIN
   IF p_domain IS NULL OR p_domain <> lower(btrim(p_domain)) OR char_length(p_domain) > 253
      OR p_domain !~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
      OR p_domain ~ '^deleted-[0-9a-f]{8,32}\.invalid$' THEN
      RAISE EXCEPTION 'purge_candidates: not a plain domain' USING ERRCODE = 'check_violation';
   END IF;
   RETURN QUERY
      SELECT a.tenant_id, a.id, a.deleted_at FROM accounts a WHERE a.domain = p_domain
       ORDER BY a.tenant_id, a.id;
END $$;
ALTER FUNCTION purge_candidates(text) OWNER TO account_purge_lookup;
REVOKE ALL ON FUNCTION purge_candidates(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION purge_candidates(text) TO account_purge_runner;

-- purge_deleted_account(): see the header.
-- reviewed: SECURITY DEFINER, owner account_purger (NOLOGIN, NOBYPASSRLS, member of app_rw).
-- Refuses a live account and an open research; one tenant and one account per call; dry run by
-- default of the caller's choice; deletes only the tables of the plan, scrubs only the columns
-- of the plan, never a ledger or audit row; EXECUTE for account_purge_runner only.
CREATE OR REPLACE FUNCTION purge_deleted_account(
   p_tenant uuid, p_account uuid, p_execute boolean,
   p_actor text DEFAULT 'operator:purge-account', p_allow_shared boolean DEFAULT false)
   RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   v_old_tenant text := coalesce(current_setting('app.tenant_id', true), '');
   v_old_scope  text := coalesce(current_setting('app.scope', true), '');
   v_deleted    timestamptz;
   v_domain     text;
   v_owner      text;
   v_is_tomb    boolean;
   v_runs       uuid[];
   v_run_texts  text[];
   v_threads    uuid[];
   v_prefixes   text[];
   v_keys       text[];
   v_hashes     text[];
   v_shared     bigint;
   v_steps      text[][] := ARRAY[
         ARRAY['fact_evidence', 'fact_id IN (SELECT f.id FROM facts f WHERE f.account_id = $1)'],
         ARRAY['grade_events', '(fact_id IN (SELECT f.id FROM facts f WHERE f.account_id = $1) OR run_id = ANY($2))'],
         ARRAY['verifier_findings', '(fact_id IN (SELECT f.id FROM facts f WHERE f.account_id = $1) OR run_id = ANY($2))'],
         ARRAY['claim_drops', 'run_id = ANY($2)'],
         ARRAY['report_sections', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['deliverables', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['job_posting_platform_sentences', 'run_id = ANY($2)'],
         ARRAY['job_posting_classifications', 'run_id = ANY($2)'],
         ARRAY['job_posting_observations', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['job_postings', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['tech_detections', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['tech_stack_items', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['link_archive', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['chunks', 'account_id = $1'],
         ARRAY['abm_distill_outputs', 'run_id = ANY($2)'],
         ARRAY['abm_sense_checks', 'run_id = ANY($2)'],
         ARRAY['alert_feedback', 'alert_id IN (SELECT x.id FROM alerts x WHERE x.account_id = $1)'],
         ARRAY['alert_outbox', 'alert_id IN (SELECT x.id FROM alerts x WHERE x.account_id = $1)'],
         ARRAY['alerts', 'account_id = $1'],
         ARRAY['chat_turns', 'account_id = $1'],
         ARRAY['chat_threads', 'id = ANY($5) AND NOT EXISTS (SELECT 1 FROM chat_turns t WHERE t.thread_id = chat_threads.id AND t.account_id IS DISTINCT FROM $1)'],
         ARRAY['connector_items', 'account_id = $1'],
         ARRAY['coverage_row_events', 'run_id = ANY($2)'],
         ARRAY['coverage_rows', 'run_id = ANY($2)'],
         ARRAY['crm_write_events', 'write_id IN (SELECT w.id FROM crm_writes w WHERE w.account_id = $1)'],
         ARRAY['crm_writes', 'account_id = $1'],
         ARRAY['crm_mappings', 'account_id = $1'],
         ARRAY['document_deletions', '(account_id = $1 OR document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1))'],
         ARRAY['document_parts', 'document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1)'],
         ARRAY['document_passages', '(account_id = $1 OR document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1))'],
         ARRAY['document_redactions', '(account_id = $1 OR document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1))'],
         ARRAY['document_table_rows', '(account_id = $1 OR document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1))'],
         ARRAY['document_tables', '(account_id = $1 OR document_id IN (SELECT d.id FROM documents d WHERE d.account_id = $1))'],
         ARRAY['download_jobs', 'account_id = $1'],
         ARRAY['export_verifications', 'package_id IN (SELECT p.id FROM export_packages p WHERE p.account_id = $1 OR p.run_id = ANY($2))'],
         ARRAY['export_packages', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['fact_reevaluations', '(account_id = $1 OR fact_id IN (SELECT f.id FROM facts f WHERE f.account_id = $1))'],
         ARRAY['intake_drops', 'account_id = $1'],
         ARRAY['internal_deliverables', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['job_pipeline_passes', 'run_id = ANY($2)'],
         ARRAY['job_pipeline_runs', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['lead_verification_queries', 'lead_verification_id IN (SELECT v.id FROM lead_verifications v WHERE v.run_id = ANY($2))'],
         ARRAY['lead_verifications', 'run_id = ANY($2)'],
         ARRAY['leak_checks', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['link_checks', 'run_id = ANY($2)'],
         ARRAY['missing_observations', 'account_id = $1'],
         ARRAY['monitor_runs', 'account_id = $1'],
         ARRAY['monitor_configs', 'account_id = $1'],
         ARRAY['notification_deliveries', 'outbox_id IN (SELECT o.id FROM notification_outbox o WHERE o.run_id = ANY($2))'],
         ARRAY['notification_outbox', 'run_id = ANY($2)'],
         ARRAY['notifications', 'account_id = $1'],
         ARRAY['people_pipeline_runs', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['person_events', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['person_post_pulls', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['person_search_pages', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['prepulled_files', 'run_id = ANY($2)'],
         ARRAY['prepulled_intent_events', 'intent_id IN (SELECT i.id FROM prepulled_intents i WHERE i.run_id = ANY($2))'],
         ARRAY['prepulled_intents', 'run_id = ANY($2)'],
         ARRAY['pull_manifests', 'run_id = ANY($2)'],
         ARRAY['raw_files', 'sha256 IN (SELECT d.sha256 FROM documents d WHERE d.account_id = $1 AND d.sha256 IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM documents o WHERE o.sha256 = raw_files.sha256 AND o.account_id IS DISTINCT FROM $1)'],
         ARRAY['refresh_row_plans', 'refresh_run_id IN (SELECT r.id FROM refresh_runs r WHERE r.account_id = $1)'],
         ARRAY['review_samples', 'run_id = ANY($2)'],
         ARRAY['run_coverage_meta', 'run_id = ANY($2)'],
         ARRAY['run_documents', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['documents', 'account_id = $1'],
         ARRAY['run_feedback', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['run_gate_results', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['signal_event_evidence', 'event_id IN (SELECT e.id FROM signal_events e WHERE e.account_id = $1)'],
         ARRAY['signal_event_links', '(event_a IN (SELECT e.id FROM signal_events e WHERE e.account_id = $1) OR event_b IN (SELECT e.id FROM signal_events e WHERE e.account_id = $1))'],
         ARRAY['signal_history', '(account_id = $1 OR fact_id IN (SELECT f.id FROM facts f WHERE f.account_id = $1))'],
         ARRAY['view_materializations', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['view_rows', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['work_unit_events', 'run_id = ANY($2)'],
         ARRAY['work_units', 'run_id = ANY($2)'],
         ARRAY['model_reply_log', 'run_id = ANY($2)'],
         ARRAY['alert_suppressions', 'event_id IN (SELECT e.id FROM signal_events e WHERE e.account_id = $1)'],
         ARRAY['signal_events', 'account_id = $1'],
         ARRAY['delta_items', 'delta_id IN (SELECT d.id FROM deltas d WHERE d.account_id = $1)'],
         ARRAY['deltas', 'account_id = $1'],
         ARRAY['facts', 'account_id = $1'],
         ARRAY['refresh_runs', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['refresh_slots', 'account_id = $1'],
         ARRAY['checkpoint_writes', 'split_part(thread_id, '':'', 1) = $3::text AND split_part(thread_id, '':'', 2) = ANY($4)'],
         ARRAY['checkpoint_blobs', 'split_part(thread_id, '':'', 1) = $3::text AND split_part(thread_id, '':'', 2) = ANY($4)'],
         ARRAY['checkpoints', 'split_part(thread_id, '':'', 1) = $3::text AND split_part(thread_id, '':'', 2) = ANY($4)'],
         ARRAY['sources', 'account_id = $1'],
         ARRAY['person_sources', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['person_posts', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['person_profiles', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['people', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['account_source_counters', 'account_id = $1'],
         ARRAY['account_bundles', '(account_id = $1 OR run_id = ANY($2))'],
         ARRAY['tenant_rule_accounts', 'account_id = $1']
   ];
   v_scrubs     text[][] := ARRAY[
         ARRAY['account_runs', 'run_input = ''{}''::jsonb, stop_detail = NULL', 'account_id = $1 AND (run_input <> ''{}''::jsonb OR stop_detail IS NOT NULL)'],
         ARRAY['runs', 'error = ''purged''', 'account_id = $1 AND error IS NOT NULL AND error <> ''purged'''],
         ARRAY['research_jobs', 'goal = NULL, topic = CASE WHEN topic IS NULL THEN NULL ELSE ''purged'' END, failure_reason = NULL', 'account_id = $1 AND (goal IS NOT NULL OR failure_reason IS NOT NULL OR (topic IS NOT NULL AND topic <> ''purged''))'],
         ARRAY['tool_calls', 'response = NULL, response_ref = NULL', 'run_id = ANY($2) AND (response IS NOT NULL OR response_ref IS NOT NULL)'],
         ARRAY['llm_calls', 'output_json = ''{}''::jsonb', 'run_id = ANY($2) AND output_json <> ''{}''::jsonb']
   ];
   v_i          integer;
   v_n          bigint;
   v_total      bigint := 0;
   v_tables     jsonb := '{}'::jsonb;
   v_scrubbed   jsonb := '{}'::jsonb;
   v_new_domain text;
   v_len        integer;
   v_seq        bigint;
   v_prev       text;
   v_at         timestamptz;
   v_canonical  text;
   v_summary    jsonb;
BEGIN
   IF p_tenant IS NULL OR p_account IS NULL OR p_execute IS NULL THEN
      RAISE EXCEPTION 'purge_deleted_account: tenant, account and execute are required'
         USING ERRCODE = 'null_value_not_allowed';
   END IF;
   IF p_actor IS NULL OR p_actor !~ '^[A-Za-z0-9:_.@/+=-]{1,255}$' THEN
      RAISE EXCEPTION 'purge_deleted_account: the actor must be a plain identifier'
         USING ERRCODE = 'check_violation';
   END IF;
   -- the tenant policies see exactly this one tenant, in the system scope (the owner policies)
   PERFORM set_config('app.tenant_id', p_tenant::text, true);
   PERFORM set_config('app.scope', 'system', true);
   PERFORM pg_advisory_xact_lock(hashtextextended('purge:' || p_account::text, 0));

   SELECT a.deleted_at, a.domain, a.owner_user_id INTO v_deleted, v_domain, v_owner
     FROM accounts a WHERE a.id = p_account AND a.tenant_id = p_tenant FOR UPDATE;
   IF NOT FOUND THEN
      RAISE EXCEPTION 'purge_deleted_account: no such account in this tenant'
         USING ERRCODE = 'no_data_found';
   END IF;
   IF v_deleted IS NULL THEN
      RAISE EXCEPTION 'purge_deleted_account: the account is still active (not deleted in the app); nothing was changed'
         USING ERRCODE = 'check_violation';
   END IF;
   v_is_tomb := v_domain ~ '^deleted-[0-9a-f]{8,32}\.invalid$';
   IF EXISTS (SELECT 1 FROM account_runs r WHERE r.account_id = p_account
                 AND r.status NOT IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELED', 'BUDGET_EXCEEDED'))
      OR EXISTS (SELECT 1 FROM research_jobs j WHERE j.account_id = p_account
                 AND j.status IN ('waiting_credit_limit', 'queued', 'running')
                 AND NOT EXISTS (SELECT 1 FROM account_runs e WHERE e.id = j.run_id
                    AND e.status IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELED', 'BUDGET_EXCEEDED')))
   THEN
      RAISE EXCEPTION 'purge_deleted_account: a research of this account is still open; stop it first'
         USING ERRCODE = 'object_in_use';
   END IF;

   SELECT coalesce(array_agg(x.id), '{}'::uuid[]) INTO v_runs FROM (
      SELECT r.id FROM account_runs r WHERE r.account_id = p_account
      UNION SELECT u.id FROM runs u WHERE u.account_id = p_account) x;
   SELECT coalesce(array_agg(x::text), '{}'::text[]) INTO v_run_texts FROM unnest(v_runs) x;
   SELECT coalesce(array_agg(DISTINCT c.thread_id), '{}'::uuid[]) INTO v_threads
     FROM chat_turns c WHERE c.account_id = p_account;

   -- what lives outside the database: prefixes (derived from ids) and exact keys (from rows)
   v_prefixes := ARRAY['tenants/' || p_tenant || '/accounts/' || p_account || '/',
                       p_tenant || '/' || p_account || '/']::text[]
      || coalesce((SELECT array_agg(p) FROM unnest(v_runs) r,
            LATERAL (VALUES ('tenants/' || p_tenant || '/internal/' || r || '/'),
                            ('tenants/' || p_tenant || '/exports/' || r || '/'),
                            ('quarantine/' || p_tenant || '/' || r || '/')) v(p)), '{}'::text[])
      || coalesce((SELECT array_agg('tenants/' || p_tenant || '/exports/' || j.id || '/')
                     FROM download_jobs j WHERE j.account_id = p_account), '{}'::text[]);
   SELECT coalesce(array_agg(DISTINCT k.key), '{}'::text[]) INTO v_keys FROM (
      SELECT d.quarantine_key AS key FROM documents d
       WHERE d.account_id = p_account AND d.quarantine_key IS NOT NULL
      UNION SELECT 'tenants/' || p_tenant || '/documents/' || d.id || '/raw'
        FROM documents d WHERE d.account_id = p_account
      UNION SELECT f.object_key FROM raw_files f
       WHERE f.sha256 IN (SELECT d.sha256 FROM documents d
                           WHERE d.account_id = p_account AND d.sha256 IS NOT NULL)
         AND NOT EXISTS (SELECT 1 FROM documents o
                          WHERE o.sha256 = f.sha256 AND o.account_id IS DISTINCT FROM p_account)) k;
   -- keys of the research cache that point at this account's calls (Redis index entries)
   SELECT coalesce(array_agg(DISTINCT t.args_hash), '{}'::text[]) INTO v_hashes
     FROM tool_calls t WHERE t.run_id = ANY(v_runs) AND t.response_ref IS NOT NULL;
   -- calls of OTHER accounts that adopted a payload stored under this account's prefix
   SELECT count(*) INTO v_shared FROM tool_calls t
    WHERE t.response_ref IS NOT NULL AND t.run_id <> ALL(v_runs)
      AND starts_with(t.response_ref, 'tenants/' || p_tenant || '/accounts/' || p_account || '/');
   IF p_execute AND v_shared > 0 AND NOT p_allow_shared THEN
      RAISE EXCEPTION 'purge_deleted_account: % call(s) of other accounts point at payloads stored under this account; nothing was changed',
         v_shared USING ERRCODE = 'object_in_use';
   END IF;

   FOR v_i IN 1 .. array_length(v_steps, 1) LOOP
      IF p_execute THEN
         EXECUTE format('DELETE FROM %I WHERE %s', v_steps[v_i][1], v_steps[v_i][2])
            USING p_account, v_runs, p_tenant, v_run_texts, v_threads;
         GET DIAGNOSTICS v_n = ROW_COUNT;
      ELSE
         EXECUTE format('SELECT count(*) FROM %I WHERE %s', v_steps[v_i][1], v_steps[v_i][2])
            INTO v_n USING p_account, v_runs, p_tenant, v_run_texts, v_threads;
      END IF;
      v_tables := v_tables || jsonb_build_object(v_steps[v_i][1], v_n);
      v_total := v_total + v_n;
   END LOOP;
   FOR v_i IN 1 .. array_length(v_scrubs, 1) LOOP
      IF p_execute THEN
         EXECUTE format('UPDATE %I SET %s WHERE %s', v_scrubs[v_i][1], v_scrubs[v_i][2],
                        v_scrubs[v_i][3])
            USING p_account, v_runs, p_tenant, v_run_texts, v_threads;
         GET DIAGNOSTICS v_n = ROW_COUNT;
      ELSE
         EXECUTE format('SELECT count(*) FROM %I WHERE %s', v_scrubs[v_i][1], v_scrubs[v_i][3])
            INTO v_n USING p_account, v_runs, p_tenant, v_run_texts, v_threads;
      END IF;
      v_scrubbed := v_scrubbed || jsonb_build_object(v_scrubs[v_i][1], v_n);
      v_total := v_total + v_n;
   END LOOP;

   IF p_execute AND NOT v_is_tomb THEN
      -- the tombstone: a unique, valid, unreachable hostname; nothing of the client remains
      FOREACH v_len IN ARRAY ARRAY[8, 12, 32] LOOP
         v_new_domain := 'deleted-' || substr(replace(p_account::text, '-', ''), 1, v_len)
                         || '.invalid';
         EXIT WHEN NOT EXISTS (SELECT 1 FROM accounts a WHERE a.tenant_id = p_tenant
                                  AND a.owner_user_id = v_owner AND a.domain = v_new_domain);
      END LOOP;
      UPDATE accounts SET domain = v_new_domain, name = NULL, industry = NULL,
             pursuing_client = NULL, careers_url = NULL, careers_url_verified_at = NULL,
             careers_fetch_ref = NULL, entity_lock = NULL, entity_lock_at = NULL,
             watch_config_json = '{}'::jsonb, primary_lane = NULL, lanes = '{}'::text[],
             refresh_cadence = NULL, next_refresh_at = NULL, library_account_id = NULL,
             updated_at = now()
       WHERE id = p_account AND tenant_id = p_tenant;
   END IF;

   v_summary := jsonb_build_object(
      'tenant_id', p_tenant, 'account_id', p_account, 'executed', p_execute,
      'deleted_at', v_deleted, 'already_tombstone', v_is_tomb, 'rows_total', v_total,
      'tables', v_tables, 'scrubbed', v_scrubbed, 'shared_payload_refs', v_shared,
      'object_prefixes', to_jsonb(v_prefixes), 'object_keys', to_jsonb(v_keys),
      'cache_args_hashes', to_jsonb(v_hashes));

   IF p_execute AND (v_total > 0 OR NOT v_is_tomb) THEN
      -- (the `:` + `null` pieces are split: Alembic would read a bare colon-word as a bind parameter)
      -- one audit row on the tenant's hash chain (src/audit/events.py canonical form; the time
      -- is whole seconds so the canonical text matches what the Python verifier recomputes)
      PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || p_tenant::text, 0));
      SELECT e.seq, e.hash INTO v_seq, v_prev FROM audit_events e
       WHERE e.tenant_id = p_tenant ORDER BY e.seq DESC LIMIT 1;
      v_seq := coalesce(v_seq, 0) + 1;
      v_prev := coalesce(v_prev, repeat('0', 64));
      v_at := date_trunc('second', clock_timestamp());
      v_canonical := '{"action":"account.purged","actor":"' || p_actor
         || '","actor_kind":"operator","at":"'
         || to_char(v_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'
         || '","audit_class":"admin","outcome":"ok","request_id":' || 'null,"resource_id":"'
         || p_account || '","resource_type":"account","route":' || 'null,"seq":' || v_seq
         || ',"support_grant_id":' || 'null}';
      INSERT INTO audit_events (tenant_id, seq, at, actor, actor_kind, audit_class, action,
                                resource_type, resource_id, route, request_id, outcome,
                                support_grant_id, dedupe_key, prev_hash, hash)
      VALUES (p_tenant, v_seq, v_at, p_actor, 'operator', 'admin', 'account.purged', 'account',
              p_account::text, NULL, NULL, 'ok', NULL, NULL, v_prev,
              encode(sha256(convert_to(v_prev || '|' || v_canonical, 'UTF8')), 'hex'));
      -- the counts: content-free, one row per account
      INSERT INTO tombstones (subject_type, subject_id, tenant_id, reason, content_sha256, at)
      VALUES ('account', p_account::text, p_tenant,
              'purged ' || jsonb_build_object('tables', v_tables, 'scrubbed', v_scrubbed)::text,
              NULL, v_at)
      ON CONFLICT (subject_type, subject_id) DO NOTHING;
   END IF;

   PERFORM set_config('app.tenant_id', v_old_tenant, true);
   PERFORM set_config('app.scope', v_old_scope, true);
   RETURN v_summary;
END $$;
ALTER FUNCTION purge_deleted_account(uuid, uuid, boolean, text, boolean) OWNER TO account_purger;
REVOKE ALL ON FUNCTION purge_deleted_account(uuid, uuid, boolean, text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION purge_deleted_account(uuid, uuid, boolean, text, boolean)
   TO account_purge_runner;
