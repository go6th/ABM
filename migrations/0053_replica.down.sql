-- Reverse 0053_replica: drop the replica tables and columns, restore the earlier constraints
-- and the earlier password-membership lookup.
ALTER TABLE connector_items DROP CONSTRAINT ck_connector_items_state;
DELETE FROM connector_items WHERE state <> 'quarantined';
ALTER TABLE connector_items ADD CONSTRAINT ck_connector_items_state CHECK (state = 'quarantined');
ALTER TABLE connector_connections DROP CONSTRAINT ck_connector_connections_kind;
DELETE FROM connector_connections WHERE kind IN ('outlook', 'onedrive', 'sharepoint');
ALTER TABLE connector_connections ADD CONSTRAINT ck_connector_connections_kind CHECK (kind IN (
   'google_drive', 'salesforce', 'hubspot', 'gmail', 'call_notes'));
DROP INDEX IF EXISTS ix_connector_items_owner;
ALTER TABLE connector_items DROP CONSTRAINT IF EXISTS ck_connector_items_snippet;
ALTER TABLE connector_items DROP CONSTRAINT IF EXISTS ck_connector_items_kind;
ALTER TABLE connector_items DROP COLUMN location, DROP COLUMN item_date, DROP COLUMN snippet,
   DROP COLUMN title, DROP COLUMN item_kind, DROP COLUMN account_id, DROP COLUMN owner_user_id;
ALTER TABLE connector_connections DROP COLUMN last_synced_at, DROP COLUMN provider, DROP COLUMN owner_user_id;

DROP TABLE isolation_checks;
DROP POLICY library_visible ON library_accounts;
DROP TABLE library_publications;
DROP TABLE library_accounts;
DROP TABLE library_uploads;

ALTER TABLE chat_turns DROP CONSTRAINT ck_chat_turns_answer_json;
ALTER TABLE chat_turns DROP COLUMN answer_json;

DROP TABLE question_categories;

CREATE OR REPLACE FUNCTION local_auth_memberships(p_user_id uuid)
   RETURNS TABLE (tenant_id uuid, user_id text, role text)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT m.tenant_id, m.user_id, m.role
     FROM memberships m
     JOIN users tu ON tu.tenant_id = m.tenant_id AND tu.subject = m.user_id
     JOIN local_identities u ON u.id = p_user_id
    WHERE u.status = 'active' AND u.email_verified_at IS NOT NULL AND m.status = 'active'
      AND lower(btrim(tu.email)) = u.email
      AND NOT COALESCE((
         SELECT e.event = 'suspended' FROM tenant_events e
          WHERE e.tenant_id = m.tenant_id AND e.event IN ('suspended', 'reactivated')
          ORDER BY e.seq DESC LIMIT 1), false)
    ORDER BY m.tenant_id
$$;
REVOKE SELECT (access_state) ON memberships FROM identity_authenticator;
ALTER TABLE memberships DROP CONSTRAINT ck_memberships_ended;
ALTER TABLE memberships DROP CONSTRAINT ck_memberships_access_state;
ALTER TABLE memberships DROP COLUMN ended_at, DROP COLUMN last_active_at, DROP COLUMN access_state;

DROP TABLE notifications;
DROP TABLE credit_entries;
DROP TABLE research_jobs;

DROP INDEX IF EXISTS ix_accounts_next_refresh;
DROP INDEX IF EXISTS ix_accounts_tenant_added_by;
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS ck_accounts_cadence;
ALTER TABLE accounts DROP CONSTRAINT ck_accounts_refresh_scope;
ALTER TABLE accounts DROP CONSTRAINT ck_accounts_origin;
ALTER TABLE accounts DROP COLUMN library_account_id, DROP COLUMN refresh_scope,
   DROP COLUMN next_refresh_at, DROP COLUMN lanes, DROP COLUMN origin, DROP COLUMN added_by;

DROP TABLE client_settings;
DROP TABLE platform_defaults;
