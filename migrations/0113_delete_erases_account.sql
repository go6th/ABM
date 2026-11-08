-- Delete in the app erases the account (owner order 2026-11-08): until now Delete only marked the
-- row (deleted_at) and adding the same domain again brought every row back. The API now calls
-- purge_own_deleted_account() right after marking the account deleted, in the same transaction,
-- so the account's rows are erased (purge_deleted_account, 0096/0112: config/deletion.yaml
-- account_purge) and its row becomes the deleted-<id>.invalid tombstone; adding the domain again
-- starts a new, empty account. Accounts deleted before this stay as they are (owner decision).

-- reviewed: SECURITY DEFINER, owner account_purger (NOLOGIN, NOBYPASSRLS, a member of app_rw, so
-- the caller's own row policies apply to its lookup); the tenant and the user come from the
-- caller's transaction (app.tenant_id, app.user_id, user scope), never from an argument; the
-- account must be that user's own and already deleted; search_path pinned; EXECUTE for app_rw only.
CREATE FUNCTION purge_own_deleted_account(
   p_account uuid, p_execute boolean, p_allow_shared boolean)
   RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   v_tenant uuid;
   v_user   text := nullif(current_setting('app.user_id', true), '');
   v_scope  text := coalesce(nullif(current_setting('app.scope', true), ''), 'user');
BEGIN
   BEGIN
      v_tenant := nullif(current_setting('app.tenant_id', true), '')::uuid;
   EXCEPTION WHEN invalid_text_representation THEN
      v_tenant := NULL;
   END;
   IF v_tenant IS NULL OR v_user IS NULL OR v_scope <> 'user' THEN
      RAISE EXCEPTION 'purge_own_deleted_account: a signed-in user transaction is required'
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   IF p_account IS NULL OR p_execute IS NULL OR p_allow_shared IS NULL THEN
      RAISE EXCEPTION 'purge_own_deleted_account: account, execute and allow_shared are required'
         USING ERRCODE = 'null_value_not_allowed';
   END IF;
   IF NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id = p_account AND a.tenant_id = v_tenant
                     AND a.owner_user_id = v_user AND a.deleted_at IS NOT NULL) THEN
      RAISE EXCEPTION 'purge_own_deleted_account: no such deleted account of this user'
         USING ERRCODE = 'no_data_found';
   END IF;
   RETURN purge_deleted_account(v_tenant, p_account, p_execute,
      'user:' || left(regexp_replace(v_user, '[^A-Za-z0-9:_.@/+=-]', '_', 'g'), 250),
      p_allow_shared);
END $$;
ALTER FUNCTION purge_own_deleted_account(uuid, boolean, boolean) OWNER TO account_purger;
REVOKE ALL ON FUNCTION purge_own_deleted_account(uuid, boolean, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION purge_own_deleted_account(uuid, boolean, boolean) TO app_rw;
