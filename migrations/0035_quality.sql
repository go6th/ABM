-- Migration 0035: quality system (cross-cutting X3; expand only). Spec
-- docs/crosscutting/CONFORMANCE.md QS1 to QS3. down_revision 0034.
--
-- release_checks: insert-only. One row per candidate: the three layers (structural
--   gate, semantic verifier, statistical eval), the eval score and the verdict.
--   The newest released row is the regression baseline (quality.regression).
-- analyst_label_tasks: one quarterly labelling task; the seed, exclusions, sample
--   and due date are stored so the draw is reproducible and its overdue state visible.
-- analyst_labels: insert-only analyst labels; disagreements become eval cases.
-- Platform tables (no tenant): quality engineering data, not tenant content.

CREATE TABLE release_checks (
   id         bigserial NOT NULL,
   candidate  text NOT NULL,
   gate_ok    boolean NOT NULL,
   verify_ok  boolean NOT NULL,
   eval_ok    boolean NOT NULL,
   eval_score numeric,
   released   boolean NOT NULL,
   at         timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_release_checks PRIMARY KEY (id),
   CONSTRAINT ck_release_checks_score CHECK (eval_score IS NULL OR eval_score >= 0)
);
CREATE INDEX ix_release_checks_released ON release_checks (released, id DESC);

CREATE TABLE analyst_label_tasks (
   id       bigserial NOT NULL,
   quarter  text NOT NULL CHECK (quarter ~ '^[0-9]{4}-Q[1-4]$'),
   seed     integer NOT NULL,
   excluded jsonb NOT NULL DEFAULT '[]'::jsonb,
   items    jsonb NOT NULL,
   due_at   timestamptz NOT NULL,
   status   text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
   created_at timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_analyst_label_tasks PRIMARY KEY (id),
   CONSTRAINT ck_analyst_label_tasks_json CHECK (jsonb_typeof(items) = 'array'
      AND jsonb_typeof(excluded) = 'array')
);

CREATE TABLE analyst_labels (
   id        bigserial NOT NULL,
   task_id   bigint NOT NULL,
   item_ref  text NOT NULL,
   label     text NOT NULL,
   analyst   text NOT NULL,
   note      text NOT NULL DEFAULT '',
   created_at timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_analyst_labels PRIMARY KEY (id),
   CONSTRAINT fk_analyst_labels_task FOREIGN KEY (task_id) REFERENCES analyst_label_tasks (id)
);

GRANT SELECT, INSERT ON release_checks, analyst_label_tasks, analyst_labels TO app_rw;
GRANT USAGE ON SEQUENCE release_checks_id_seq, analyst_label_tasks_id_seq,
   analyst_labels_id_seq TO app_rw;
REVOKE UPDATE, DELETE ON release_checks, analyst_label_tasks, analyst_labels FROM app_rw;
