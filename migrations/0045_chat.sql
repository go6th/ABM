-- Tenant-scoped chat history and an atomic per-conversation spend/turn ledger.
-- Secrets remain in the API runtime; no provider credential is stored in these tables.

CREATE TABLE chat_threads (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants (id),
   owner_user_id  text NOT NULL,
   title          text NOT NULL DEFAULT '',
   turn_count     integer NOT NULL DEFAULT 0,
   spent_usd      numeric(14,8) NOT NULL DEFAULT 0,
   reserved_usd   numeric(14,8) NOT NULL DEFAULT 0,
   created_at     timestamptz NOT NULL DEFAULT now(),
   updated_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_chat_threads PRIMARY KEY (id),
   CONSTRAINT uq_chat_threads_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_chat_threads_title CHECK (char_length(title) <= 160),
   CONSTRAINT ck_chat_threads_turn_count CHECK (turn_count >= 0),
   CONSTRAINT ck_chat_threads_costs CHECK (spent_usd >= 0 AND reserved_usd >= 0)
);
ALTER TABLE chat_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_threads FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON chat_threads TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE, DELETE ON chat_threads TO app_rw;
CREATE INDEX ix_chat_threads_owner_updated
   ON chat_threads (tenant_id, owner_user_id, updated_at DESC, id DESC);

CREATE TABLE chat_turns (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   thread_id      uuid NOT NULL,
   turn_number    integer NOT NULL,
   request_id     uuid NOT NULL,
   model_id       text NOT NULL,
   user_content   text NOT NULL,
   assistant_content text,
   client         text NOT NULL DEFAULT 'web',
   account_id     uuid,
   phase          text NOT NULL DEFAULT 'chat',
   status         text NOT NULL DEFAULT 'reserved',
   input_tokens   integer NOT NULL DEFAULT 0,
   output_tokens  integer NOT NULL DEFAULT 0,
   cache_read_tokens integer NOT NULL DEFAULT 0,
   cache_write_tokens integer NOT NULL DEFAULT 0,
   reserved_usd   numeric(14,8) NOT NULL,
   cost_usd       numeric(14,8) NOT NULL DEFAULT 0,
   created_at     timestamptz NOT NULL DEFAULT now(),
   completed_at   timestamptz,
   CONSTRAINT pk_chat_turns PRIMARY KEY (id),
   CONSTRAINT uq_chat_turns_thread_turn UNIQUE (tenant_id, thread_id, turn_number),
   CONSTRAINT uq_chat_turns_thread_request UNIQUE (tenant_id, thread_id, request_id),
   CONSTRAINT fk_chat_turns_thread FOREIGN KEY (tenant_id, thread_id)
      REFERENCES chat_threads (tenant_id, id) ON DELETE CASCADE,
   CONSTRAINT fk_chat_turns_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_chat_turns_number CHECK (turn_number > 0),
   CONSTRAINT ck_chat_turns_content CHECK (char_length(user_content) BETWEEN 1 AND 60000
      AND (assistant_content IS NULL OR char_length(assistant_content) <= 100000)),
   CONSTRAINT ck_chat_turns_client_phase CHECK (client = 'web' AND phase = 'chat'),
   CONSTRAINT ck_chat_turns_status CHECK (status IN ('reserved', 'completed', 'failed')),
   CONSTRAINT ck_chat_turns_usage CHECK (input_tokens >= 0 AND output_tokens >= 0
      AND cache_read_tokens >= 0 AND cache_write_tokens >= 0),
   CONSTRAINT ck_chat_turns_cost CHECK (reserved_usd >= 0 AND cost_usd >= 0),
   CONSTRAINT ck_chat_turns_completion CHECK (
      (status = 'reserved' AND assistant_content IS NULL AND completed_at IS NULL)
      OR (status = 'completed' AND assistant_content IS NOT NULL AND completed_at IS NOT NULL)
      OR (status = 'failed' AND assistant_content IS NULL AND completed_at IS NOT NULL)
   )
);
ALTER TABLE chat_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_turns FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON chat_turns TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON chat_turns TO app_rw;
CREATE INDEX ix_chat_turns_thread_order
   ON chat_turns (tenant_id, thread_id, turn_number);
